#!/usr/bin/env bash
# ==========================================
# AeroSync 私有化一键部署脚本
# 使用：bash scripts/init-private.sh
# ==========================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.private.yml"

echo "🚁 AeroSync 私有化部署脚本"
echo "======================================"

# 1. 检查 Docker
docker --version >/dev/null 2>&1 || { echo "❌ Docker 未安装，请先安装 Docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "❌ Docker Compose 未安装"; exit 1; }

# 2. 检查组成文件
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ 未找到 $COMPOSE_FILE"
    exit 1
fi

# 3. 构建镜像
echo "🔨 正在构建 API / Worker 镜像..."
cd "$PROJECT_DIR"
docker compose -f "$COMPOSE_FILE" build api worker

# 4. 启动基础服务（先启动 DB / Redis / MinIO）
echo "🚀 启动基础服务 (PostgreSQL, Redis, MinIO)..."
docker compose -f "$COMPOSE_FILE" up -d postgres redis minio

# 5. 等待服务就绪
echo "⏳ 等待数据库和存储服务就绪..."
sleep 8

# 6. 创建 MinIO bucket
echo "📦 初始化 MinIO bucket..."
docker run --rm --network aerosync-private \
  minio/mc:latest \
  sh -c "
    mc alias set local http://minio:9000 minioadmin minioadmin >/dev/null 2>&1
    mc mb local/aerosync >/dev/null 2>&1 || echo 'Bucket 已存在或创建中...'
    mc anonymous set download local/aerosync >/dev/null 2>&1 || true
  " || echo "⚠️ MinIO bucket 初始化失败，应用启动后会自动重试"

# 7. 启动 Ollama
echo "🤖 启动 Ollama (第一次会自动拉取 qwen2.5:7b，约 4-8GB，请耐心等待)..."
docker compose -f "$COMPOSE_FILE" up -d ollama

# 8. 启动 API 和 Worker
echo "📡 启动 API 与 Worker..."
docker compose -f "$COMPOSE_FILE" up -d api worker

# 9. 等待 API 就绪
sleep 5
API_URL="http://localhost:8000/api/v1/health"
for i in {1..12}; do
    if curl -sf "$API_URL" >/dev/null 2>&1; then
        echo "✅ API 已就绪"
        break
    fi
    echo "  等待 API 就绪... ($i/12)"
    sleep 3
done

# 10. 打印信息
echo ""
echo "======================================"
echo "🎉 AeroSync 私有化部署完成！"
echo "======================================"
echo ""
echo "🔗 访问地址:"
echo "   API        : http://localhost:8000"
echo "   Admin UI   : http://localhost:8000/admin"
echo "   MinIO Console: http://localhost:9001  (账号 minioadmin / minioadmin)"
echo "   Ollama API : http://localhost:11434"
echo ""
echo "📊 常用命令:"
echo "   查看日志: docker compose -f docker-compose.private.yml logs -f api"
echo "   停止服务: docker compose -f docker-compose.private.yml down"
echo "   完全清理: docker compose -f docker-compose.private.yml down -v"
echo ""
echo "⚠️  请第一时间修改默认 Token："
echo "   环境变量 API_TOKEN 在 docker-compose.private.yml 中"
echo ""
