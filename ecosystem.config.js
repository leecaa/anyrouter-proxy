module.exports = {
  apps: [
    {
      name: "anyrouter-prod",
      script: "/home/ubuntu/github/anyrouter/main.py",
      interpreter: "/home/ubuntu/github/anyrouter/.venv/bin/python",
      cwd: "/home/ubuntu/github/anyrouter",
      args: "--host 127.0.0.1 --port 8765",
      autorestart: true,
      watch: false,
      max_memory_restart: "250M", // 内存保护，防止 Python Outbound 大并发测试时内存溢出
      env: {
        NODE_ENV: "production",
        ANYROUTER_PROXY_CONFIG: "/home/ubuntu/.config/anyrouter-proxy/proxy_config.json"
      },
      error_file: "/home/ubuntu/.pm2/logs/anyrouter-prod-error.log",
      out_file: "/home/ubuntu/.pm2/logs/anyrouter-prod-out.log",
      log_merge: true,
      time: true // 在 PM2 日志中打印优雅的时间戳
    }
  ]
};
