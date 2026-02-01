 # AWS Deployment Guide - Siphon Strategies Executor

This guide walks you through deploying the Siphon Strategies Executor microservices to AWS.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         AWS VPC                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    EC2 Instance                           │   │
│  │              (c5.2xlarge / c6i.2xlarge)                  │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │   │
│  │  │   Trade     │  │    FHE      │  │    Payload      │  │   │
│  │  │  Executor   │◄─┤   Engine    │◄─┤   Generator     │  │   │
│  │  │   :5005     │  │   :5001     │  │     :5009       │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                    Application Load Balancer                     │
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                           Internet
```

## Prerequisites

- AWS CLI installed and configured
- Docker installed locally
- AWS Account with appropriate permissions

## Step 1: Create ECR Repositories

```bash
# Set your AWS region
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create ECR repositories for each service
aws ecr create-repository --repository-name siphon-trade-executor --region $AWS_REGION
aws ecr create-repository --repository-name siphon-fhe-engine --region $AWS_REGION
aws ecr create-repository --repository-name siphon-payload-generator --region $AWS_REGION
```

## Step 2: Build and Push Images

```bash
# Login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Build images
docker-compose -f docker-compose.prod.yml build

# Tag and push trade-executor
docker tag siphon-trade-executor:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/siphon-trade-executor:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/siphon-trade-executor:latest

# Tag and push fhe-engine
docker tag syphon-fhe-engine:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/siphon-fhe-engine:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/siphon-fhe-engine:latest

# Tag and push payload-generator
docker tag siphon-payload-generator:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/siphon-payload-generator:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/siphon-payload-generator:latest
```

## Step 3: Launch EC2 Instance

### Recommended Instance Types

For FHE (Fully Homomorphic Encryption) workloads, use compute-optimized instances:

| Instance Type | vCPUs | Memory | Best For |
|--------------|-------|--------|----------|
| `c5.2xlarge` | 8 | 16 GB | Development/Testing |
| `c5.4xlarge` | 16 | 32 GB | Production (Recommended) |
| `c6i.4xlarge` | 16 | 32 GB | Production (Latest gen) |

### Launch Commands

```bash
# Create a security group
aws ec2 create-security-group \
    --group-name siphon-sg \
    --description "Security group for Siphon services"

# Allow inbound traffic
aws ec2 authorize-security-group-ingress \
    --group-name siphon-sg \
    --protocol tcp \
    --port 22 \
    --cidr 0.0.0.0/0

aws ec2 authorize-security-group-ingress \
    --group-name siphon-sg \
    --protocol tcp \
    --port 5005 \
    --cidr 0.0.0.0/0

aws ec2 authorize-security-group-ingress \
    --group-name siphon-sg \
    --protocol tcp \
    --port 5009 \
    --cidr 0.0.0.0/0

# Launch instance (Amazon Linux 2023)
aws ec2 run-instances \
    --image-id ami-0c7217cdde317cfec \
    --instance-type c5.2xlarge \
    --key-name your-key-pair \
    --security-groups siphon-sg \
    --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":50,"VolumeType":"gp3"}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=siphon-executor}]'
```

## Step 4: Configure EC2 Instance

SSH into your instance and run:

```bash
# Update system
sudo yum update -y

# Install Docker
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Log out and back in for docker group to take effect
exit
```

## Step 5: Deploy Services

```bash
# SSH back into the instance
ssh -i your-key.pem ec2-user@<instance-public-ip>

# Login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Create deployment directory
mkdir -p ~/siphon && cd ~/siphon

# Create .env file (copy from .env.prod.template and fill in values)
cat > .env << 'EOF'
AWS_ACCOUNT_ID=your-account-id
AWS_REGION=us-east-1
VERSION=latest
API_TOKEN=your-secure-api-token
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
EXECUTOR_PRIVATE_KEY=your-private-key
SYPHON_VAULT_CONTRACT_ADDRESS=your-contract-address
ARKIV_RPC_URL=https://your-arkiv-endpoint
EOF

# Create docker-compose.prod.yml (or copy from your local machine)
# scp -i your-key.pem docker-compose.prod.yml ec2-user@<ip>:~/siphon/

# Pull and start services
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d

# Check status
docker-compose -f docker-compose.prod.yml ps
docker-compose -f docker-compose.prod.yml logs -f
```

## Step 6: Set Up Monitoring (Optional but Recommended)

### CloudWatch Logs

```bash
# Install CloudWatch agent
sudo yum install -y amazon-cloudwatch-agent

# Configure agent to collect Docker logs
sudo cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'EOF'
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/lib/docker/containers/*/*.log",
            "log_group_name": "/siphon/containers",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
EOF

sudo systemctl start amazon-cloudwatch-agent
sudo systemctl enable amazon-cloudwatch-agent
```

## Step 7: Set Up Load Balancer (Production)

For production, set up an Application Load Balancer:

```bash
# Create target groups
aws elbv2 create-target-group \
    --name siphon-trade-executor-tg \
    --protocol HTTP \
    --port 5005 \
    --vpc-id <your-vpc-id> \
    --health-check-path /health

aws elbv2 create-target-group \
    --name siphon-payload-generator-tg \
    --protocol HTTP \
    --port 5009 \
    --vpc-id <your-vpc-id> \
    --health-check-path /health
```

## Useful Commands

```bash
# View logs
docker-compose -f docker-compose.prod.yml logs -f trade-executor
docker-compose -f docker-compose.prod.yml logs -f fhe-engine

# Restart a service
docker-compose -f docker-compose.prod.yml restart trade-executor

# Stop all services
docker-compose -f docker-compose.prod.yml down

# Update to new version
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d

# Check resource usage
docker stats
```

## Troubleshooting

### FHE Engine Slow Performance

If FHE operations are slow, ensure:
1. Instance type has sufficient CPU (c5.2xlarge minimum)
2. Docker has access to all CPU cores: `docker info | grep CPUs`

### Out of Memory

Increase memory limits in docker-compose.prod.yml or use a larger instance type.

### Health Check Failing

Check if the `/health` endpoint is implemented in each service. If not, update the health check to use a different endpoint or remove it temporarily.

### Connection Refused Between Services

Ensure services are on the same Docker network:
```bash
docker network ls
docker network inspect siphon-network
```

## Security Recommendations

1. **Use AWS Secrets Manager** for sensitive values instead of environment variables
2. **Enable VPC** and place services in private subnets
3. **Use HTTPS** with ACM certificates on the load balancer
4. **Enable IAM roles** for EC2 instead of access keys
5. **Set up VPC Flow Logs** for network monitoring
