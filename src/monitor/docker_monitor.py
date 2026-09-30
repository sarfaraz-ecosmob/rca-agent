"""
Docker Container Monitor for AI RCA Agent.

Discovers running containers, watches for new/removed containers,
and manages log streaming sessions for each container.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, Optional, Set

import aiohttp
import aiodocker
from aiodocker.containers import DockerContainer
from aiodocker.exceptions import DockerError
from aiodocker.utils import clean_filters

from config.settings import get_settings
from src.db.database import async_session_factory
from src.db.models import ContainerModel

logger = logging.getLogger(__name__)
settings = get_settings()


class DockerMonitor:
    """
    Monitors Docker containers via the Docker socket.
    
    Features:
    - Auto-discovers running containers
    - Watches for new container creation events
    - Detects container removal
    - Manages container monitoring lifecycle
    - Supports all logging drivers (json-file, journald, syslog)
    """

    def __init__(self):
        self.docker: Optional[aiodocker.Docker] = None
        self._monitored_containers: Dict[str, DockerContainer] = {}
        self._container_tasks: Dict[str, asyncio.Task] = {}
        self._scan_task: Optional[asyncio.Task] = None
        self._event_task: Optional[asyncio.Task] = None
        self._running = False
        self._discovered_ids: Set[str] = set()
        self._on_log_callback = None
        self._on_container_stop_callback = None

    async def connect(self) -> None:
        """Connect to Docker daemon."""
        try:
            docker_host = os.environ.get("DOCKER_HOST") or "unix:///var/run/docker.sock"
            logger.info(f"Connecting to Docker at {docker_host}")
            try:
                # Build the aiohttp session explicitly so we can control two
                # things aiodocker's default session gets wrong for our use:
                #
                # 1. No overall request timeout. aiohttp's default session
                #    timeout is total=300s, and aiodocker applies it to EVERY
                #    request on the shared session — including follow=True
                #    log streams, which are idle by design (no log output =
                #    no bytes on the wire). After exactly 5 minutes each
                #    stream dies with a bare TimeoutError and must reconnect.
                #    The aiodocker authors note this themselves in
                #    events.py: "timeout has to be set to 0 ... Otherwise
                #    after 5 minutes the client will close the connection".
                # 2. Unlimited connection pool (limit=0). With the default
                #    limit=100, leaked/dying streams eventually exhaust the
                #    pool and every Docker API call in the process hangs.
                #
                # NOTE: when a connector is supplied, aiodocker skips its own
                # URL normalization, so we must pass the dummy host URL its
                # constructor would otherwise build for unix sockets
                # ("unix://localhost") — the real socket path lives in the
                # connector itself.
                if docker_host.startswith("unix://"):
                    connector = aiohttp.UnixConnector(
                        docker_host[len("unix://"):], limit=0
                    )
                    client_url = "unix://localhost"
                else:
                    connector = aiohttp.TCPConnector(limit=0)
                    client_url = docker_host
                session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=None),
                )
                self.docker = aiodocker.Docker(
                    url=client_url, connector=connector, session=session
                )
            except Exception:
                # Fallback to aiodocker's own session setup, but still
                # disable the 5-minute default timeout on it.
                self.docker = aiodocker.Docker(url=docker_host)
                try:
                    self.docker.session._timeout = aiohttp.ClientTimeout(total=None)
                except Exception:
                    pass

            # Verify connection
            version = await self.docker.version()
            logger.info(
                f"Connected to Docker: {version.get('Version', 'unknown')} "
                f"(API {version.get('ApiVersion', 'unknown')})"
            )
        except Exception as e:
            logger.error(f"Failed to connect to Docker: {e!r}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Docker daemon."""
        self._running = False
        if self.docker:
            await self.docker.close()
            self.docker = None
        logger.info("Disconnected from Docker.")

    async def start_monitoring(
        self,
        on_log_callback=None,
        on_container_stop_callback=None,
    ) -> None:
        """Start monitoring all running containers."""
        self._running = True
        self._on_log_callback = on_log_callback
        self._on_container_stop_callback = on_container_stop_callback

        # Start the periodic container scan
        self._scan_task = asyncio.create_task(self._scan_loop())
        
        # Start listening for Docker events
        self._event_task = asyncio.create_task(self._event_listener())

        logger.info("Container monitoring started.")

    async def stop_monitoring(self) -> None:
        """Stop all container monitoring."""
        self._running = False

        # Cancel all container tasks
        for container_id, task in list(self._container_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Error cancelling task for {container_id}: {e}")

        # Cancel scan and event tasks
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except (asyncio.CancelledError, Exception):
                pass

        self._container_tasks.clear()
        self._monitored_containers.clear()
        self._discovered_ids.clear()
        logger.info("Container monitoring stopped.")

    async def get_container_info(self, container: DockerContainer) -> dict:
        """Get container information."""
        try:
            info = await container.show()
            name = info["Name"].lstrip("/") if info.get("Name") else "unknown"
            return {
                "container_id": info["Id"][:12],
                "name": name,
                "image": info.get("Config", {}).get("Image", "unknown"),
                "state": info.get("State", {}).get("Status", "unknown"),
                "status": info.get("State", {}).get("Status", ""),
                "log_driver": (
                    info.get("HostConfig", {})
                    .get("LogConfig", {})
                    .get("Type", "json-file")
                ),
                "labels": info.get("Config", {}).get("Labels", {}),
                "hostname": os.uname().nodename,
            }
        except Exception as e:
            logger.warning(f"Error getting container info: {e}")
            return {}

    async def _scan_loop(self) -> None:
        """Periodically scan for container changes."""
        while self._running:
            try:
                await self._scan_containers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in container scan loop: {e}")
            
            await asyncio.sleep(settings.CONTAINER_SCAN_INTERVAL)

    async def _scan_containers(self) -> None:
        """Discover running containers and manage monitoring lifecycle."""
        if not self.docker:
            return

        try:
            # Get all running containers
            containers = await self.docker.containers.list(all=True)
            current_ids: Set[str] = set()

            for container in containers:
                try:
                    info = await container.show()
                    container_id = info["Id"][:12]
                    current_ids.add(container_id)

                    # Check if container should be monitored (running or restarting)
                    state = info.get("State", {}).get("Status", "unknown")
                    should_monitor = state in ("running", "restarting")

                    if should_monitor and container_id not in self._monitored_containers:
                        await self._start_monitoring_container(container)
                    elif not should_monitor and container_id in self._monitored_containers:
                        await self._stop_monitoring_container(container_id)

                except Exception as e:
                    logger.debug(f"Error processing container in scan: {e!r}")
                continue

            # Detect removed containers
            for container_id in list(self._monitored_containers.keys()):
                if container_id not in current_ids:
                    await self._stop_monitoring_container(container_id)

            self._discovered_ids = current_ids

        except Exception as e:
            # repr() instead of str(): connection errors like TimeoutError /
            # ConnectionRefusedError have an empty str() which made these
            # log lines unreadable ("Error scanning containers: ").
            logger.error(f"Error scanning containers: {e!r}")

    async def _start_monitoring_container(self, container: DockerContainer) -> None:
        """Start monitoring a single container."""
        try:
            info = await container.show()
            container_id = info["Id"][:12]
            name = info["Name"].lstrip("/") if info.get("Name") else container_id

            self._monitored_containers[container_id] = container

            # Persist container to database
            await self._persist_container(info)

            # Start log streaming task
            task = asyncio.create_task(
                self._stream_container_logs(container_id, container, name)
            )
            self._container_tasks[container_id] = task

            logger.info(f"Started monitoring container: {name} ({container_id})")

        except Exception as e:
            logger.error(f"Error starting monitoring for container: {e}")

    async def _stop_monitoring_container(self, container_id: str) -> None:
        """Stop monitoring a container."""
        if container_id in self._container_tasks:
            self._container_tasks[container_id].cancel()
            try:
                await self._container_tasks[container_id]
            except (asyncio.CancelledError, Exception):
                pass
            del self._container_tasks[container_id]

        if container_id in self._monitored_containers:
            del self._monitored_containers[container_id]

        # Update container in database using container_id column
        async with async_session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(ContainerModel).where(
                        ContainerModel.container_id == container_id
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.is_monitored = False
                    existing.state = "removed"
                    await session.commit()
            except Exception as e:
                logger.warning(f"Error updating container in DB: {e}")

        logger.info(f"Stopped monitoring container: {container_id}")

    async def _stream_container_logs(
        self, container_id: str, container: DockerContainer, name: str
    ) -> None:
        """
        Stream logs from a container in real-time.
        
        Supports:
        - json-file logging driver (streaming)
        - journald logging driver (polling fallback)
        - syslog logging driver (polling fallback)
        - Both stdout and stderr
        """
        log_driver = "json-file"
        try:
            info = await container.show()
            log_driver = (
                info.get("HostConfig", {})
                .get("LogConfig", {})
                .get("Type", "json-file")
            )
        except Exception:
            pass

        logger.info(f"Starting log stream for {name} (driver: {log_driver})")

        while self._running and container_id in self._monitored_containers:
            try:
                if log_driver == "json-file":
                    await self._stream_jsonfile_logs(container, container_id, name)
                else:
                    # Fallback: poll logs for journald/syslog drivers
                    await self._poll_container_logs(container, container_id, name)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # repr() so bare TimeoutError / ConnectionRefusedError (whose
                # str() is empty) still produce a readable log line.
                logger.warning(
                    f"Log stream interrupted for {name}: {e!r}. Reconnecting..."
                )
                await asyncio.sleep(2)

            # The stream loop above returned/ended. If the container is no
            # longer running (exited / crashed / removed), tear down its
            # monitoring cleanly instead of leaking the task and its Docker
            # API connection. This is critical: a leaked follow-stream holds
            # a pooled connection forever, and enough leaks exhaust the
            # aiodocker connection pool for the WHOLE process (all Docker
            # API calls then hang), which stops container discovery too.
            try:
                info = await container.show()
                state = info.get("State", {}).get("Status", "")
                if state not in ("running", "restarting"):
                    logger.info(
                        f"Container {name} is no longer running (state: {state}); "
                        "stopping log stream"
                    )
                    # Remove our own bookkeeping first so
                    # _stop_monitoring_container does not try to cancel this
                    # very task from within itself.
                    self._container_tasks.pop(container_id, None)
                    self._monitored_containers.pop(container_id, None)
                    if self._on_container_stop_callback:
                        try:
                            await self._on_container_stop_callback(container_id, name)
                        except Exception as cb_err:
                            logger.warning(
                                f"Container stop callback failed for {name}: {cb_err!r}"
                            )
                    await self._stop_monitoring_container(container_id)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Post-stream state check failed for {name}: {e!r}")
                # Avoid a hot spin if the container was removed (show() keeps
                # failing); the scan loop will reap it within one interval.
                await asyncio.sleep(1)

    async def _stream_jsonfile_logs(
        self, container: DockerContainer, container_id: str, name: str
    ) -> None:
        """Stream logs using json-file driver (supports follow)."""
        try:
            async for log_line in container.log(
                follow=True,
                stdout=True,
                stderr=True,
                timestamps=True,
                tail=10,
            ):
                if not self._running:
                    break
                if self._on_log_callback:
                    await self._on_log_callback(
                        container_id=container_id,
                        container_name=name,
                        log_line=log_line,
                    )
        except DockerError as e:
            # 404: container gone; 409: container not running (e.g. it exited
            # while we were attaching) — both are normal lifecycle events.
            if e.status in (404, 409):
                logger.debug(f"Container {name} log stream ended (status {e.status})")
            else:
                raise
        except Exception as e:
            if "404" in str(e) or "409" in str(e):
                logger.debug(f"Container {name} log stream ended (container stopped)")
            else:
                raise

    async def _poll_container_logs(
        self, container: DockerContainer, container_id: str, name: str
    ) -> None:
        """Poll logs for non-json-file logging drivers."""
        last_timestamp = datetime.utcnow()

        while self._running and container_id in self._monitored_containers:
            try:
                # Check if container is still running
                info = await container.show()
                state = info.get("State", {}).get("Status", "")
                if state not in ("running", "restarting"):
                    logger.info(f"Container {name} is no longer running (state: {state})")
                    if self._on_container_stop_callback:
                        await self._on_container_stop_callback(container_id, name)
                    break

                # Get logs since last check. Docker's `since` filter expects
                # a Unix timestamp — a naive datetime str() is silently
                # misinterpreted by the API, so convert explicitly.
                logs = await container.log(
                    stdout=True,
                    stderr=True,
                    timestamps=True,
                    tail=100,
                    since=int(last_timestamp.timestamp()),
                )

                for log_line in logs:
                    if self._on_log_callback:
                        await self._on_log_callback(
                            container_id=container_id,
                            container_name=name,
                            log_line=log_line,
                        )

                last_timestamp = datetime.utcnow()
                await asyncio.sleep(settings.LOG_POLL_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error polling logs for {name}: {e}")
                await asyncio.sleep(5)

    async def _event_listener(self) -> None:
        """
        Listen for Docker events to detect new/stopped containers.

        Uses aiodocker's subscribe() channel API (the run()-based generator
        API is not usable in this library version: run() is a plain
        coroutine that streams internally, so awaiting it and iterating the
        result never yields events — new/started containers were only ever
        picked up by the periodic scan).
        """
        if not self.docker:
            return

        while self._running:
            try:
                # subscribe() spawns the background stream task and returns a
                # ChannelSubscriber queue.
                subscriber = self.docker.events.subscribe()

                while self._running:
                    try:
                        # wait_for keeps the listener responsive to shutdown
                        # even when no events arrive.
                        event = await asyncio.wait_for(subscriber.get(), timeout=30)
                    except asyncio.TimeoutError:
                        continue

                    # The channel publishes None when the background stream
                    # task terminates (connection lost) — reconnect.
                    if event is None:
                        logger.warning("Docker events channel closed; reconnecting...")
                        break

                    event_type = event.get("Type", "")
                    action = event.get("Action", "")
                    actor = event.get("Actor", {})
                    event_id = actor.get("ID", event.get("id", ""))[:12]

                    if event_type == "container":
                        if action in ("start", "create", "restart"):
                            if event_id not in self._monitored_containers:
                                logger.info(f"New container event detected: {action} - {event_id}")
                                # Will be picked up by next scan
                                await asyncio.sleep(1)
                                await self._scan_containers()

                        elif action in ("die", "stop", "destroy", "kill"):
                            if event_id in self._monitored_containers:
                                logger.info(f"Container stop event: {action} - {event_id}")
                                await self._stop_monitoring_container(event_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in event listener: {e!r}")

            # Make sure the library's background event-stream task is fully
            # stopped before retrying, otherwise subscribe() will not spawn a
            # new one (it guards on an existing task).
            try:
                await self.docker.events.stop()
            except Exception:
                pass

            if self._running:
                await asyncio.sleep(5)

    async def _persist_container(self, info: dict) -> None:
        """Save or update container info in database."""
        async with async_session_factory() as session:
            try:
                container_id = info["Id"][:12]
                name = info["Name"].lstrip("/") if info.get("Name") else container_id
                state = info.get("State", {}).get("Status", "unknown")
                log_driver = (
                    info.get("HostConfig", {})
                    .get("LogConfig", {})
                    .get("Type", "json-file")
                )

                from sqlalchemy import select

                # Mark old containers with the same name but different ID as stopped
                # (handles container recreations where Docker assigns a new ID)
                # Skip file-log sources so custom log file monitoring is unaffected.
                old_containers = await session.execute(
                    select(ContainerModel).where(
                        ContainerModel.name == name,
                        ContainerModel.container_id != container_id,
                        ContainerModel.is_monitored == True,
                        ContainerModel.image != "file-log",
                    )
                )
                for old in old_containers.scalars().all():
                    old.is_monitored = False
                    old.state = "removed"
                    logger.info(f"Marked old container record as removed: {old.name} ({old.container_id})")

                # Upsert container using container_id column (not PK)
                result = await session.execute(
                    select(ContainerModel).where(
                        ContainerModel.container_id == container_id
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.name = name
                    existing.state = state
                    existing.status = info.get("State", {}).get("Status", "")
                    existing.is_monitored = True
                    existing.last_seen = datetime.utcnow()
                else:
                    container = ContainerModel(
                        container_id=container_id,
                        name=name,
                        image=info.get("Config", {}).get("Image", "unknown"),
                        state=state,
                        status=info.get("State", {}).get("Status", ""),
                        log_driver=log_driver,
                        labels=info.get("Config", {}).get("Labels", {}),
                        hostname=os.uname().nodename,
                    )
                    session.add(container)

                await session.commit()

            except Exception as e:
                logger.warning(f"Error persisting container: {e!r}")
                await session.rollback()

    @property
    def monitored_containers(self) -> Dict[str, DockerContainer]:
        """Get currently monitored containers."""
        return self._monitored_containers

    @property
    def monitored_count(self) -> int:
        """Get count of monitored containers."""
        return len(self._monitored_containers)
