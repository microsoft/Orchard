"""Configuration settings for the orchestrator."""

from typing import List, Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""
    
    # Service settings
    service_name: str = "sandbox-orchestrator"
    host: str = "0.0.0.0"
    port: int = 8000
    
    # API Key Authentication
    api_keys: Optional[str] = None  # Comma-separated list of valid API keys
    require_api_key: bool = True  # Set to False to disable auth (for internal use)
    
    # Kubernetes settings
    in_cluster: bool = True
    namespace_prefix: str = "sbx-"
    orchestrator_namespace: str = "orchestrator"
    sandbox_namespace: str = "sandbox-pods"  # Shared namespace for all sandbox pods
    
    # Sandbox settings
    default_cpu: str = "4"
    default_memory: str = "16Gi"
    default_working_dir: str = "/workspace"
    default_block_network: bool = True
    sandbox_node_selector: dict = {"workload": "sandbox"}
    
    # Execution settings
    max_concurrent_execs: int = 400
    default_timeout_seconds: int = 300
    
    # K8s API throttling
    k8s_api_concurrency: int = 100  # Max concurrent K8s API calls per replica
    k8s_connect_timeout: int = 10  # TCP connect timeout for K8s API calls (seconds)
    k8s_api_timeout: int = 30  # Read timeout for K8s API calls (seconds)
    k8s_api_retries: int = 3  # Retry transient K8s API errors (keep low to release slots fast)
    k8s_api_pool_size: int = 100  # urllib3 connection pool size for K8s API
    k8s_exec_concurrency: int = 200  # Max concurrent exec WebSocket connections per replica
    k8s_exec_retries: int = 3  # Retry exec connection timeouts
    max_concurrent_creates: int = 20  # Max concurrent sandbox creates per replica
    
    # Agent settings (in-pod HTTP agent for exec / file operations)
    agent_port: int = 8080  # Port the sandbox agent listens on
    agent_connect_timeout: int = 5  # TCP connect timeout to agent (seconds)
    agent_pool_size: int = 500  # aiohttp connection pool size for agent calls
    agent_injector_image: str = "sandbox-agent-injector:latest"  # Init container image that injects agent into any sandbox
    
    # TTL settings
    sandbox_ttl_hours: int = 2
    orphan_job_ttl_hours: int = 1
    cleanup_interval_seconds: int = 300  # 5 minutes
    pending_timeout_buffer_seconds: int = 600  # Extra buffer beyond creation_timeout for pending sandboxes
    
    # Heartbeat settings
    heartbeat_timeout_seconds: int = 180  # 3 minutes without heartbeat = dead
    heartbeat_cleanup_enabled: bool = True  # Enable heartbeat-based cleanup
    
    # Redis settings (for multi-replica state sharing)
    redis_url: str = "redis://redis-service.orchestrator.svc.cluster.local:6379/0"
    use_redis: bool = True  # Set to False to use in-memory store (single replica only)
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
    
    def get_api_keys_set(self) -> set:
        """Get the set of valid API keys.
        
        Supports keys separated by commas, spaces, or newlines.
        """
        if not self.api_keys:
            return set()
        # Split by comma, space, or newline to handle all common formats
        import re
        return set(key.strip() for key in re.split(r'[,\s]+', self.api_keys) if key.strip())


settings = Settings()
