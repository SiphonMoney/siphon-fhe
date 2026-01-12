"""
Authentication and Rate Limiting Module
"""
from functools import wraps
from flask import request, jsonify
from datetime import datetime, timedelta
from collections import defaultdict
import os
from threading import Lock

# Rate limiting storage
rate_limit_store = defaultdict(list)
rate_limit_lock = Lock()

def require_auth(f):
    """Decorator to require API token authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('X-API-TOKEN') or request.headers.get('Authorization')
        
        if not token:
            return jsonify({"error": "Missing authentication token"}), 401
        
        # Remove 'Bearer ' prefix if present
        if token.startswith('Bearer '):
            token = token[7:]
        
        expected_token = os.getenv('API_TOKEN', '')
        if not expected_token:
            # Development mode - allow any token
            print("⚠️  WARNING: API_TOKEN not set, allowing all requests")
            return f(*args, **kwargs)
        
        if token != expected_token:
            return jsonify({"error": "Invalid authentication token"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

def rate_limit(max_requests=100, window_seconds=60):
    """Decorator for rate limiting"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get client identifier
            client_id = request.headers.get('X-Client-ID') or request.remote_addr
            
            now = datetime.now()
            window_start = now - timedelta(seconds=window_seconds)
            
            with rate_limit_lock:
                # Clean old requests
                rate_limit_store[client_id] = [
                    req_time for req_time in rate_limit_store[client_id]
                    if req_time > window_start
                ]
                
                # Check limit
                if len(rate_limit_store[client_id]) >= max_requests:
                    return jsonify({
                        "error": "Rate limit exceeded",
                        "limit": max_requests,
                        "window_seconds": window_seconds
                    }), 429
                
                # Add current request
                rate_limit_store[client_id].append(now)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_client_id():
    """Get client identifier for rate limiting"""
    return request.headers.get('X-Client-ID') or request.remote_addr
