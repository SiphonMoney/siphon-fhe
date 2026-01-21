// Authentication middleware for FHE Engine
use axum::extract::Request;
use axum::middleware::Next;
use axum::response::Response;
use axum::http::{StatusCode, HeaderMap};
use std::env;

pub async fn auth_middleware(
    request: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    // Check for API token in header
    let headers = request.headers();
    let token = headers
        .get("X-API-TOKEN")
        .or_else(|| headers.get("Authorization"))
        .and_then(|h| h.to_str().ok())
        .map(|s| s.strip_prefix("Bearer ").unwrap_or(s).to_string());
    
    let token = match token {
        Some(t) => t,
        None => {
            // In development, allow requests without token if API_TOKEN not set
            let expected_token = env::var("API_TOKEN").ok();
            if expected_token.is_none() {
                println!("⚠️  WARNING: API_TOKEN not set, allowing request");
                return Ok(next.run(request).await);
            }
            return Err(StatusCode::UNAUTHORIZED);
        }
    };
    
    // Verify token
    let expected_token = env::var("API_TOKEN")
        .unwrap_or_else(|_| "".to_string());
    
    if expected_token.is_empty() {
        println!("⚠️  WARNING: API_TOKEN not set, allowing request");
        return Ok(next.run(request).await);
    }
    
    if token != expected_token {
        return Err(StatusCode::UNAUTHORIZED);
    }
    
    Ok(next.run(request).await)
}
