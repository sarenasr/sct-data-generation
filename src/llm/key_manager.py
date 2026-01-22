"""API Key Manager for handling multiple API keys with rate limit rotation."""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

from ..logging import get_logger

logger = get_logger(__name__)


class KeyStatus(Enum):
    """Status of an API key."""
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    EXHAUSTED = "exhausted"
    ERROR = "error"


@dataclass
class APIKeyState:
    """State tracking for a single API key."""
    key: str
    status: KeyStatus = KeyStatus.AVAILABLE
    request_count: int = 0
    last_used: Optional[float] = None
    rate_limit_reset_time: Optional[float] = None
    error_count: int = 0
    last_error: Optional[str] = None


class AllKeysExhaustedException(Exception):
    """Exception raised when all API keys are exhausted."""
    def __init__(self, message: str = "All API keys have been exhausted"):
        self.message = message
        super().__init__(self.message)


class APIKeyManager:
    """
    Manages multiple API keys with automatic rotation on rate limits.
    
    Features:
    - Round-robin key selection
    - Automatic rotation when a key hits rate limits
    - Tracking of key status and usage
    - Graceful handling when all keys are exhausted
    """

    def __init__(self, api_keys: List[str], provider: str = "openai"):
        """
        Initialize the API Key Manager.

        Args:
            api_keys: List of API keys to manage.
            provider: LLM provider name for logging ("openai" or "gemini").
        """
        self.provider = provider
        self.keys: Dict[str, APIKeyState] = {}
        self.key_order: List[str] = []
        self.current_index: int = 0
        
        # Initialize key states
        for key in api_keys:
            if key and key not in self.keys:  # Skip empty and duplicate keys
                self.keys[key] = APIKeyState(key=key)
                self.key_order.append(key)
        
        if not self.keys:
            raise ValueError(f"No valid API keys provided for {provider}")
        
        logger.info(f"APIKeyManager initialized with {len(self.keys)} {provider} API key(s)")

    def get_current_key(self) -> str:
        """
        Get the current active API key.

        Returns:
            The current API key.

        Raises:
            AllKeysExhaustedException: If all keys are exhausted or rate limited.
        """
        self._refresh_rate_limited_keys()
        
        available_key = self._find_available_key()
        if available_key:
            return available_key
        
        # Check if any keys are just rate limited (not exhausted)
        rate_limited_keys = [
            k for k, v in self.keys.items() 
            if v.status == KeyStatus.RATE_LIMITED
        ]
        
        if rate_limited_keys:
            # Find the key with the earliest reset time
            earliest_reset = min(
                self.keys[k].rate_limit_reset_time 
                for k in rate_limited_keys 
                if self.keys[k].rate_limit_reset_time
            )
            wait_time = max(0, earliest_reset - time.time())
            raise AllKeysExhaustedException(
                f"All {self.provider} API keys are rate limited. "
                f"Earliest reset in {wait_time:.0f} seconds."
            )
        
        raise AllKeysExhaustedException(
            f"All {self.provider} API keys have been exhausted."
        )

    def _find_available_key(self) -> Optional[str]:
        """Find the next available key using round-robin."""
        start_index = self.current_index
        
        for _ in range(len(self.key_order)):
            key = self.key_order[self.current_index]
            state = self.keys[key]
            
            if state.status == KeyStatus.AVAILABLE:
                return key
            
            self.current_index = (self.current_index + 1) % len(self.key_order)
            
            if self.current_index == start_index:
                break
        
        return None

    def _refresh_rate_limited_keys(self):
        """Check and refresh any keys whose rate limit period has expired."""
        current_time = time.time()
        
        for key, state in self.keys.items():
            if state.status == KeyStatus.RATE_LIMITED:
                if state.rate_limit_reset_time and current_time >= state.rate_limit_reset_time:
                    state.status = KeyStatus.AVAILABLE
                    state.rate_limit_reset_time = None
                    logger.info(f"API key ***{key[-4:]} is now available again")

    def mark_success(self, key: str):
        """
        Mark a successful request for a key.

        Args:
            key: The API key that was used successfully.
        """
        if key in self.keys:
            state = self.keys[key]
            state.request_count += 1
            state.last_used = time.time()
            state.error_count = 0  # Reset error count on success

    def mark_rate_limited(self, key: str, retry_after: Optional[float] = None):
        """
        Mark a key as rate limited.

        Args:
            key: The API key that hit rate limit.
            retry_after: Seconds until the rate limit resets (if known).
        """
        if key in self.keys:
            state = self.keys[key]
            state.status = KeyStatus.RATE_LIMITED
            
            # Default to 60 seconds if not specified
            wait_time = retry_after or 60.0
            state.rate_limit_reset_time = time.time() + wait_time
            
            logger.warning(
                f"API key ***{key[-4:]} rate limited. "
                f"Will retry after {wait_time:.0f} seconds."
            )
            
            # Move to next key
            self.current_index = (self.current_index + 1) % len(self.key_order)

    def mark_exhausted(self, key: str, reason: str = ""):
        """
        Mark a key as permanently exhausted (e.g., quota exceeded).

        Args:
            key: The API key that is exhausted.
            reason: Reason for exhaustion.
        """
        if key in self.keys:
            state = self.keys[key]
            state.status = KeyStatus.EXHAUSTED
            state.last_error = reason
            
            logger.error(f"API key ***{key[-4:]} exhausted: {reason}")
            
            # Move to next key
            self.current_index = (self.current_index + 1) % len(self.key_order)

    def mark_error(self, key: str, error: str):
        """
        Mark an error for a key (non-rate-limit error).

        Args:
            key: The API key that encountered an error.
            error: Error message.
        """
        if key in self.keys:
            state = self.keys[key]
            state.error_count += 1
            state.last_error = error
            
            # After 5 consecutive errors, mark as exhausted
            if state.error_count >= 5:
                self.mark_exhausted(key, f"Too many errors: {error}")

    def rotate_key(self):
        """Force rotation to the next available key."""
        self.current_index = (self.current_index + 1) % len(self.key_order)
        logger.debug(f"Rotated to next API key")

    def get_stats(self) -> Dict:
        """
        Get statistics about key usage.

        Returns:
            Dictionary with key statistics.
        """
        stats = {
            "provider": self.provider,
            "total_keys": len(self.keys),
            "available": 0,
            "rate_limited": 0,
            "exhausted": 0,
            "total_requests": 0,
            "keys": []
        }
        
        for key, state in self.keys.items():
            key_info = {
                "key_suffix": f"***{key[-4:]}",
                "status": state.status.value,
                "request_count": state.request_count,
                "error_count": state.error_count,
            }
            stats["keys"].append(key_info)
            stats["total_requests"] += state.request_count
            
            if state.status == KeyStatus.AVAILABLE:
                stats["available"] += 1
            elif state.status == KeyStatus.RATE_LIMITED:
                stats["rate_limited"] += 1
            elif state.status == KeyStatus.EXHAUSTED:
                stats["exhausted"] += 1
        
        return stats

    def has_available_keys(self) -> bool:
        """Check if there are any available keys."""
        self._refresh_rate_limited_keys()
        return any(
            state.status == KeyStatus.AVAILABLE 
            for state in self.keys.values()
        )

    def all_keys_exhausted(self) -> bool:
        """Check if all keys are permanently exhausted."""
        return all(
            state.status == KeyStatus.EXHAUSTED 
            for state in self.keys.values()
        )
