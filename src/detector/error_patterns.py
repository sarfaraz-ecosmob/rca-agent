"""
Error pattern definitions for the AI RCA Agent.

Defines regex patterns and severity mappings for all error categories:
- Application Errors
- Infrastructure Errors
- Database Errors
- Container Errors
- Web Server Errors
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern

from src.models.schemas import ErrorCategory, Severity


@dataclass
class ErrorPattern:
    """Definition of an error pattern to match against log lines."""

    name: str
    pattern: str
    category: ErrorCategory
    severity: Severity
    description: str = ""
    is_regex: bool = True
    compiled: Optional[Pattern] = None

    def __post_init__(self):
        if self.is_regex and not self.compiled:
            try:
                self.compiled = re.compile(self.pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{self.pattern}': {e}")

    def match(self, text: str) -> Optional[re.Match]:
        """Match the pattern against text."""
        if self.compiled:
            return self.compiled.search(text)
        return self.pattern.lower() in text.lower()


# --- Application Error Patterns ---

APPLICATION_ERRORS: List[ErrorPattern] = [
    ErrorPattern(
        name="exception",
        pattern=r"\b(?:uncaught\s+)?exception\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.HIGH,
        description="General application exception",
    ),
    ErrorPattern(
        name="unhandled_exception",
        pattern=r"unhandled\s+exception",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Unhandled application exception",
    ),
    ErrorPattern(
        name="error",
        pattern=r"\berror\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.MEDIUM,
        description="General application error",
    ),
    ErrorPattern(
        name="fatal",
        pattern=r"\bfatal\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Fatal application error",
    ),
    ErrorPattern(
        name="panic",
        pattern=r"\bpanic\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Application panic (Go/Rust)",
    ),
    ErrorPattern(
        name="traceback",
        pattern=r"\bTraceback\b.*",
        category=ErrorCategory.APPLICATION,
        severity=Severity.HIGH,
        description="Python traceback",
    ),
    ErrorPattern(
        name="segmentation_fault",
        pattern=r"\b(?:segmentation\s*fault|segfault|SIGSEGV)\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Segmentation fault",
    ),
    ErrorPattern(
        name="null_pointer",
        pattern=r"\b(?:NullPointerException|null\s*reference|NPE)\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Null pointer exception (Java)",
    ),
    ErrorPattern(
        name="stack_overflow",
        pattern=r"\b(?:stack\s*overflow|StackOverflowError)\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Stack overflow error",
    ),
    ErrorPattern(
        name="out_of_memory",
        pattern=r"\b(?:OutOfMemoryError|OOM|out\s*of\s*memory)\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Out of memory error",
    ),
    ErrorPattern(
        name="assertion_error",
        pattern=r"\b(?:AssertionError|assertion\s*failed)\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.HIGH,
        description="Assertion error",
    ),
    ErrorPattern(
        name="class_not_found",
        pattern=r"\b(?:ClassNotFoundException|NoClassDefFoundError)\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.HIGH,
        description="Class not found (Java)",
    ),
    ErrorPattern(
        name="illegal_argument",
        pattern=r"\bIllegalArgument\s*Exception\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.MEDIUM,
        description="Illegal argument exception",
    ),
    ErrorPattern(
        name="index_out_of_bounds",
        pattern=r"\b(?:IndexOutOfBoundsException|ArrayIndexOutOfBounds)\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.HIGH,
        description="Index out of bounds",
    ),
    ErrorPattern(
        name="key_error",
        pattern=r"\bKeyError\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.MEDIUM,
        description="Key error (Python dict)",
    ),
    ErrorPattern(
        name="type_error",
        pattern=r"\bTypeError\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.MEDIUM,
        description="Type error",
    ),
    ErrorPattern(
        name="value_error",
        pattern=r"\bValueError\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.MEDIUM,
        description="Value error",
    ),
    ErrorPattern(
        name="attribute_error",
        pattern=r"\bAttributeError\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.MEDIUM,
        description="Attribute error (Python)",
    ),
    ErrorPattern(
        name="runtime_error",
        pattern=r"\bRuntimeError\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.HIGH,
        description="Runtime error",
    ),
    ErrorPattern(
        name="abort",
        pattern=r"\bABORTING?\b",
        category=ErrorCategory.APPLICATION,
        severity=Severity.CRITICAL,
        description="Application abort",
    ),
]

# --- Infrastructure Error Patterns ---

INFRASTRUCTURE_ERRORS: List[ErrorPattern] = [
    ErrorPattern(
        name="connection_refused",
        pattern=r"\b(?:connection\s+refused|ECONNREFUSED)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.CRITICAL,
        description="Connection refused by remote host",
    ),
    ErrorPattern(
        name="connection_reset",
        pattern=r"\b(?:connection\s+reset|ECONNRESET|connection\s+closed\s+by\s+remote)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.HIGH,
        description="Connection reset by peer",
    ),
    ErrorPattern(
        name="timeout",
        pattern=r"\b(?:timeout|timed?\s*out|ETIMEDOUT|time\s*out)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.MEDIUM,
        description="Operation timeout",
    ),
    ErrorPattern(
        name="dns_failure",
        pattern=r"\b(?:DNS\s*(?:resolution\s*)?failure|NameOrServiceNotKnown|"
                r"DNS\s*error|cannot\s*resolve|TemporaryFailureInNameResolution)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.HIGH,
        description="DNS resolution failure",
    ),
    ErrorPattern(
        name="disk_full",
        pattern=r"\b(?:disk\s*full|no\s*space\s*left\s*on\s*device|ENOSPC)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.CRITICAL,
        description="Disk space exhausted",
    ),
    ErrorPattern(
        name="memory_exhausted",
        pattern=r"\b(?:memory\s*(?:exhausted|full|pressure)|cannot\s*allocate\s*memory|"
                r"ENOMEM|out\s*of\s*virtual\s*memory)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.CRITICAL,
        description="Memory exhausted",
    ),
    ErrorPattern(
        name="cpu_throttling",
        pattern=r"\b(?:cpu\s*(?:throttled?|pressure|quota)|throttling)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.MEDIUM,
        description="CPU throttling detected",
    ),
    ErrorPattern(
        name="oom_killed",
        pattern=r"\b(?:OOMKilled|oom\s*kill|killed\s*by\s*OOM|memory\s*cgroup\s*limit)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.CRITICAL,
        description="Container killed by OOM",
    ),
    ErrorPattern(
        name="permission_denied",
        pattern=r"\b(?:permission\s*denied|EACCES|EPERM)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.MEDIUM,
        description="Permission denied",
    ),
    ErrorPattern(
        name="network_unreachable",
        pattern=r"\b(?:network\s*(?:is\s*)?unreachable|ENETUNREACH|"
                r"network\s*error|network\s*not\s*available)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.HIGH,
        description="Network unreachable",
    ),
    ErrorPattern(
        name="io_error",
        pattern=r"\b(?:IOError|IOException|I/O\s*error)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.MEDIUM,
        description="I/O error",
    ),
    ErrorPattern(
        name="file_not_found",
        pattern=r"\b(?:FileNotFoundError|NoSuchFile|file\s*not\s*found)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.MEDIUM,
        description="File not found",
    ),
    ErrorPattern(
        name="port_unavailable",
        pattern=r"\b(?:port\s*(?:already\s*)?in\s*use|EADDRINUSE|address\s*already\s*in\s*use)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.HIGH,
        description="Port already in use",
    ),
    ErrorPattern(
        name="disk_iowait",
        pattern=r"\b(?:iowait|i/o\s*wait|disk\s*latency)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.MEDIUM,
        description="Disk I/O wait high",
    ),
    ErrorPattern(
        name="load_average",
        pattern=r"\b(?:load\s*average|high\s*load)\b",
        category=ErrorCategory.INFRASTRUCTURE,
        severity=Severity.MEDIUM,
        description="High system load",
    ),
]

# --- Database Error Patterns ---

DATABASE_ERRORS: List[ErrorPattern] = [
    # MySQL / MariaDB
    ErrorPattern(
        name="mysql_connection",
        pattern=r"\b(?:mysql|maria)[\w\s]*error.*(?:connect|lost|gone|timeout)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="MySQL/MariaDB connection error",
    ),
    ErrorPattern(
        name="mysql_deadlock",
        pattern=r"\b(?:deadlock|DeadlockFound|lock\s*wait\s*timeout)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.HIGH,
        description="Database deadlock detected",
    ),
    ErrorPattern(
        name="mysql_query_error",
        pattern=r"\b(?:MySQLSyntaxErrorException|ER_PARSE_ERROR|"
                r"SQL\s*syntax|duplicate\s*entry|IntegrityConstraintViolation)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.MEDIUM,
        description="MySQL query error",
    ),
    ErrorPattern(
        name="mysql_max_connections",
        pattern=r"\b(?:max_connections|too\s*many\s*connections)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="MySQL max connections reached",
    ),
    
    # PostgreSQL
    ErrorPattern(
        name="postgresql_connection",
        pattern=r"\b(?:postgres|pgsql)[\w\s]*error.*(?:connect|lost|reject)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="PostgreSQL connection error",
    ),
    ErrorPattern(
        name="postgresql_deadlock",
        pattern=r"\b(?:deadlock\s*detected|could\s*not\s*serialize)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.HIGH,
        description="PostgreSQL deadlock detected",
    ),
    ErrorPattern(
        name="postgresql_disk_full",
        pattern=r"\b(?:no\s*space\s*left|archiver\s*failed|"
                r"could\s*not\s*write\s*to\s*file)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="PostgreSQL disk full",
    ),
    ErrorPattern(
        name="postgresql_replication",
        pattern=r"\b(?:replication\s*(?:lag|conflict|error)|"
                r"standby\s*connection\s*failed)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.HIGH,
        description="PostgreSQL replication error",
    ),
    
    # MongoDB
    ErrorPattern(
        name="mongodb_connection",
        pattern=r"\b(?:mongo)[\w\s]*error.*(?:connect|timeout|refused)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="MongoDB connection error",
    ),
    ErrorPattern(
        name="mongodb_replication",
        pattern=r"\b(?:replica\s*set.*(?:error|down)|"
                r"primary\s*stepdown|replication\s*lag)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.HIGH,
        description="MongoDB replication error",
    ),
    ErrorPattern(
        name="mongodb_cursor",
        pattern=r"\b(?:cursor\s*(?:not\s*found|timeout)|"
                r"ExceededTimeLimit|operation\s*timed\s*out)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.MEDIUM,
        description="MongoDB cursor timeout",
    ),
    
    # Redis
    ErrorPattern(
        name="redis_connection",
        pattern=r"\b(?:redis)[\w\s]*error.*(?:connect|refused|timeout)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="Redis connection error",
    ),
    ErrorPattern(
        name="redis_max_memory",
        pattern=r"\b(?:OOM\s*command\s*not\s*allowed|maxmemory|"
                r"memory\s*limit|MISCONF)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.HIGH,
        description="Redis memory limit reached",
    ),
    ErrorPattern(
        name="redis_cluster",
        pattern=r"\b(?:CLUSTERDOWN|cluster\s*down|slot\s*not\s*covered)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="Redis cluster error",
    ),
    
    # Elasticsearch
    ErrorPattern(
        name="elasticsearch_connection",
        pattern=r"\b(?:elasticsearch|es)[\w\s]*error.*(?:connect|timeout)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="Elasticsearch connection error",
    ),
    ErrorPattern(
        name="elasticsearch_cluster",
        pattern=r"\b(?:cluster\s*(?:health|status).*(?:red|yellow)|"
                r"master_not_discovered|discovery\s*failed)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="Elasticsearch cluster unhealthy",
    ),
    ErrorPattern(
        name="elasticsearch_disk",
        pattern=r"\b(?:disk\s*usage\s*exceeded|flood\s*stage|"
                r"read_only_allow_delete)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.CRITICAL,
        description="Elasticsearch disk usage critical",
    ),
    ErrorPattern(
        name="elasticsearch_query",
        pattern=r"\b(?:search_phase_execution_exception|"
                r"index_not_found|illegal_argument)\b",
        category=ErrorCategory.DATABASE,
        severity=Severity.MEDIUM,
        description="Elasticsearch query error",
    ),
]

# --- Container Error Patterns ---

CONTAINER_ERRORS: List[ErrorPattern] = [
    ErrorPattern(
        name="crash_loop",
        pattern=r"\b(?:CrashLoop|crash\s*loop|back-off|backoff)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.CRITICAL,
        description="Container in crash loop",
    ),
    ErrorPattern(
        name="container_restart",
        pattern=r"\b(?:container.*restart|restarting\s*container|"
                r"restart\s*count|RestartPolicy)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.HIGH,
        description="Container restart detected",
    ),
    ErrorPattern(
        name="exit_code",
        pattern=r"\b(?:exited\s*with\s*code|exit\s*code\s*\d+|"
                r"ExitCode[:\s]\d+)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.MEDIUM,
        description="Container exit code",
    ),
    ErrorPattern(
        name="signal_terminated",
        pattern=r"\b(?:terminated\s*by\s*signal|signal\s*\d+|"
                r"SIGKILL|SIGTERM|SIGABRT|killed)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.HIGH,
        description="Container terminated by signal",
    ),
    ErrorPattern(
        name="health_check_failure",
        pattern=r"\b(?:health\s*check\s*(?:failed|unhealthy)|"
                r"unhealthy\s*status|liveness\s*probe|readiness\s*probe)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.HIGH,
        description="Container health check failure",
    ),
    ErrorPattern(
        name="container_oom",
        pattern=r"\b(?:container.*OOM|OOMKilled|out\s*of\s*memory:\s*container)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.CRITICAL,
        description="Container out of memory",
    ),
    ErrorPattern(
        name="image_pull_error",
        pattern=r"\b(?:image\s*pull\s*error|pull\s*access\s*denied|"
                r"manifest\s*not\s*found|not\s*found\s*:\s*\S+\s*:\s*latest)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.HIGH,
        description="Container image pull error",
    ),
    ErrorPattern(
        name="port_binding",
        pattern=r"\b(?:port\s*binding\s*failed|cannot\s*bind\s*port)\b",
        category=ErrorCategory.CONTAINER,
        severity=Severity.HIGH,
        description="Port binding failure",
    ),
]

# --- Web Server Error Patterns ---

WEB_SERVER_ERRORS: List[ErrorPattern] = [
    # Nginx
    ErrorPattern(
        name="nginx_5xx",
        pattern=r"\bnginx.*\b(?:500|502|503|504)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Nginx 5xx server error",
    ),
    ErrorPattern(
        name="nginx_4xx",
        pattern=r"\bnginx.*\b(?:403|404|429)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.MEDIUM,
        description="Nginx 4xx client error",
    ),
    ErrorPattern(
        name="nginx_upstream",
        pattern=r"\b(?:upstream\s*(?:timeout|unavailable|refused)|"
                r"no\s*live\s*upstreams|connect\s*failed)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.CRITICAL,
        description="Nginx upstream failure",
    ),
    ErrorPattern(
        name="nginx_worker",
        pattern=r"\b(?:worker\s*process.*(?:failed|error)|"
                r"open\s*socket.*(?:failed|error)|accept\s*failed)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Nginx worker process error",
    ),
    
    # Apache
    ErrorPattern(
        name="apache_5xx",
        pattern=r"\b(?:apache|httpd).*\b(?:500|502|503|504)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Apache 5xx server error",
    ),
    ErrorPattern(
        name="apache_worker",
        pattern=r"\b(?:apache|httpd).*(?:worker|child|process).*(?:error|failed)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Apache worker process error",
    ),
    ErrorPattern(
        name="apache_mpm",
        pattern=r"\b(?:mpm|server\s*reached\s*MaxRequestWorkers|"
                r"server\s*couldn't\s*start)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Apache MPM limit reached",
    ),
    
    # Node.js
    ErrorPattern(
        name="nodejs_uncaught",
        pattern=r"\b(?:node|nodejs).*(?:uncaughtException|unhandledRejection)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.CRITICAL,
        description="Node.js uncaught exception",
    ),
    ErrorPattern(
        name="nodejs_heap",
        pattern=r"\b(?:FATAL\s*ERROR|JavaScript\s*heap\s*out\s*of\s*memory|"
                r"allocation\s*failed|heap_limit)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.CRITICAL,
        description="Node.js heap error",
    ),
    ErrorPattern(
        name="nodejs_deprecated",
        pattern=r"\b(?:DeprecationWarning|deprecated)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.LOW,
        description="Node.js deprecation warning",
    ),
    
    # Python WSGI/ASGI
    ErrorPattern(
        name="python_gunicorn",
        pattern=r"\b(?:gunicorn|uvicorn|waitress).*(?:error|critical|failed)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Python WSGI/ASGI server error",
    ),
    ErrorPattern(
        name="python_django",
        pattern=r"\b(?:django).*(?:error|exception|crash)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Django error",
    ),
    ErrorPattern(
        name="python_flask",
        pattern=r"\b(?:flask|werkzeug).*(?:error|exception|crash)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Flask/Werkzeug error",
    ),
    ErrorPattern(
        name="python_import",
        pattern=r"\b(?:ImportError|ModuleNotFoundError)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Python import error",
    ),
    
    # Java
    ErrorPattern(
        name="java_tomcat",
        pattern=r"\b(?:tomcat|catalina).*(?:error|exception|failed)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Tomcat error",
    ),
    ErrorPattern(
        name="java_spring",
        pattern=r"\b(?:spring).*(?:error|exception|failed)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Spring framework error",
    ),
    ErrorPattern(
        name="java_thread",
        pattern=r"\b(?:java.*lang.*(?:Error|Exception)|"
                r"thread.*(?:error|exception|dump))\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="Java thread error",
    ),
    
    # Go
    ErrorPattern(
        name="go_panic",
        pattern=r"\b(?:http|gin|echo|fiber).*(?:panic|error|fatal)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.CRITICAL,
        description="Go HTTP server panic",
    ),
    
    # PHP
    ErrorPattern(
        name="php_fatal",
        pattern=r"\b(?:PHP\s*Fatal\s*error|PHP\s*Parse\s*error|"
                r"PHP\s*Warning|PHP\s*Notice|Uncaught\s*Error)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="PHP fatal error",
    ),
    ErrorPattern(
        name="php_fpm",
        pattern=r"\b(?:php-fpm|php_fpm).*(?:error|failed|crash)\b",
        category=ErrorCategory.WEB_SERVER,
        severity=Severity.HIGH,
        description="PHP-FPM error",
    ),
]


# --- Combined patterns lookup ---

# Category-based pattern lookup
PATTERNS_BY_CATEGORY: Dict[ErrorCategory, List[ErrorPattern]] = {
    ErrorCategory.APPLICATION: APPLICATION_ERRORS,
    ErrorCategory.INFRASTRUCTURE: INFRASTRUCTURE_ERRORS,
    ErrorCategory.DATABASE: DATABASE_ERRORS,
    ErrorCategory.CONTAINER: CONTAINER_ERRORS,
    ErrorCategory.WEB_SERVER: WEB_SERVER_ERRORS,
}

# All patterns combined
ALL_PATTERNS: List[ErrorPattern] = (
    APPLICATION_ERRORS
    + INFRASTRUCTURE_ERRORS
    + DATABASE_ERRORS
    + CONTAINER_ERRORS
    + WEB_SERVER_ERRORS
)


def get_pattern_by_name(name: str) -> Optional[ErrorPattern]:
    """Get an error pattern by its name."""
    for pattern in ALL_PATTERNS:
        if pattern.name == name:
            return pattern
    return None


def get_patterns_by_category(category: ErrorCategory) -> List[ErrorPattern]:
    """Get all patterns for a specific error category."""
    return PATTERNS_BY_CATEGORY.get(category, [])


def get_severity_for_category(category: ErrorCategory) -> Severity:
    """Get default severity for a category."""
    severity_map = {
        ErrorCategory.APPLICATION: Severity.HIGH,
        ErrorCategory.INFRASTRUCTURE: Severity.CRITICAL,
        ErrorCategory.DATABASE: Severity.CRITICAL,
        ErrorCategory.CONTAINER: Severity.HIGH,
        ErrorCategory.WEB_SERVER: Severity.MEDIUM,
        ErrorCategory.UNKNOWN: Severity.MEDIUM,
    }
    return severity_map.get(category, Severity.MEDIUM)
