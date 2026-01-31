mod handlers;
mod fhe_engine;
mod config;

use axum::{routing::{post, get}, Router, extract::DefaultBodyLimit, Json};
use handlers::evaluation_handler;
use tower_http::cors::CorsLayer;
use std::net::SocketAddr;
use serde_json::json;

#[tokio::main]
async fn main() {
    // Load environment variables from .env file
    dotenv::dotenv().ok();
    
    println!("--- Starting Syphon FHE Co-Processor (Rust - REAL COMPUTE MODE) ---");
    
    let app = Router::new()
        .route("/evaluateStrategy", post(evaluation_handler::evaluate_strategy))
        .route("/health", get(health_check))
        .layer(CorsLayer::permissive()) 
        .layer(DefaultBodyLimit::max(50000000000 * 1024 * 1024)); 

    let addr = SocketAddr::from(([0, 0, 0, 0], 5001));
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    println!("--- Listening for FHE tasks on http://localhost:5001/evaluateStrategy ---\n");
    axum::serve(listener, app).await.unwrap();
}

async fn health_check() -> Json<serde_json::Value> {
    Json(json!({"status": "healthy", "service": "fhe-engine"}))
}