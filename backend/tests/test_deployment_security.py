import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DeploymentSecurityTests(unittest.TestCase):
    def test_docker_context_excludes_secrets_and_runtime_state(self):
        lines = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.assertTrue({"data", ".env", "**/.env", "narratoai/"}.issubset(lines))

    def test_deploy_script_avoids_destructive_and_insecure_operations(self):
        script = (ROOT / "deploy.sh").read_text()
        self.assertNotIn("sshpass -p", script)
        self.assertNotIn("sshpass", script)
        self.assertIn("BatchMode=yes", script)
        self.assertNotIn("StrictHostKeyChecking=no", script)
        self.assertNotIn('docker ps --filter "publish=80"', script)
        self.assertNotIn("sudo rm -rf /home/ubuntu/douyin-auto-system", script)
        self.assertNotIn("curl -s -o /dev/null", script)

    def test_deploy_script_preserves_runtime_state_and_retries_health(self):
        script = (ROOT / "deploy.sh").read_text()
        for excluded in ("data", ".env", "narratoai/"):
            self.assertIn(f"--exclude='{excluded}'", script)
        self.assertIn("curl -sf http://localhost/api/health", script)
        self.assertNotIn("127.0.0.1:8601", script)
        self.assertNotIn("/preview/health", script)
        self.assertIn("for attempt in", script)
        self.assertNotIn("_stcore/health", script)

    def test_container_logs_are_bounded_and_legacy_streamlit_is_absent(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertGreaterEqual(compose.count('max-size: "10m"'), 2)
        self.assertGreaterEqual(compose.count('max-file: "3"'), 2)
        self.assertIn("./data:/app/data", compose)
        self.assertNotIn("narrato-engine:", compose)
        self.assertNotIn("VIDEO_HUB_NARRATO_ENGINE_URL", compose)
        self.assertNotIn("8501", compose)
        self.assertNotIn("8601", compose)


class HostDeploymentTests(unittest.TestCase):
    def test_host_routes_and_websocket_are_configured(self):
        config = (ROOT / "deploy/nginx-host.conf").read_text()
        self.assertIn("location /api/", config)
        self.assertIn("video-hub-guard.conf", config)
        listen_lines = [line.strip() for line in config.splitlines() if line.strip().startswith("listen ")]
        self.assertTrue(listen_lines)
        self.assertTrue(all("default_server" not in line for line in listen_lines))
        self.assertNotIn("location /narrato/", config)
        self.assertNotIn("location /preview/", config)
        self.assertIn("$http_upgrade", config)
        self.assertIn("try_files $uri $uri/ /index.html", config)
        self.assertNotIn("proxy_pass http://127.0.0.1:8501", config)
        self.assertNotIn("8502", config)

    def test_systemd_backend_does_not_start_local_asr_or_narrato_engine(self):
        backend = (ROOT / "deploy/systemd/video-hub-backend.service").read_text()
        self.assertNotIn("VIDEO_HUB_ASR_URL=http://127.0.0.1:8600", backend)
        self.assertNotIn("VIDEO_HUB_NARRATO_ENGINE_URL", backend)
        self.assertIn("--host 127.0.0.1 --port 8000", backend)
        self.assertFalse((ROOT / "deploy/systemd/narrato-engine.service").exists())
        self.assertFalse((ROOT / "deploy/systemd/narrato-asr.service").exists())
        self.assertFalse((ROOT / "deploy/systemd/narrato-preview.service").exists())
        self.assertFalse((ROOT / "deploy/systemd/narratoai.service").exists())

    def test_remote_install_preserves_runtime_data_and_backs_up_configuration(self):
        script = (ROOT / "deploy/remote-install.sh").read_text()
        self.assertIn("/opt/video-hub-backups", script)
        self.assertIn("cp -a /opt/NarratoAI/config.toml", script)
        self.assertIn("cp -a /opt/NarratoAI/storage", script)
        self.assertNotIn("rm -rf /opt/NarratoAI", script)
        self.assertNotIn("narratoai/.venv", script)
        self.assertNotIn("services/.venv", script)
        self.assertNotIn("无法提供 ASR 服务", script)
        self.assertIn("nginx -t", script)
        self.assertIn("migrate_legacy.py", script)
        self.assertIn('chown -R narratoai:narratoai', script)
        self.assertIn('systemctl disable --now "$unit"', script)
        for unit in ("narrato-asr", "narrato-engine", "narrato-preview", "video-hub-edit-narrate"):
            self.assertIn(unit, script)

    def test_all_writable_narrato_resources_survive_code_sync(self):
        script = (ROOT / "deploy.sh").read_text()
        installer = (ROOT / "deploy/remote-install.sh").read_text()
        self.assertIn("--exclude='narratoai/'", script)
        self.assertIn("cp -a /opt/NarratoAI/storage", installer)
        self.assertNotIn("narratoai/.venv", installer)
        for directory in ("videos", "scripts", "bgm", "songs", "srt", "fonts", "safe_areas", "tts_reference_audio"):
            self.assertIn(directory, installer)

    def test_existing_unified_stack_is_included_in_backups(self):
        installer = (ROOT / "deploy/remote-install.sh").read_text()
        for path in (
            "/etc/nginx/sites-available/video-hub",
            "/etc/nginx/sites-enabled/video-hub",
            "/etc/systemd/system/video-hub-backend.service",
            "/etc/systemd/system/narrato-engine.service",
        ):
            self.assertIn(path, installer)
        self.assertIn("systemctl enable video-hub-backend", installer)
        self.assertNotIn("systemctl enable video-hub-backend narrato-engine", installer)
        self.assertNotIn("http://127.0.0.1:8600/health", installer)
        self.assertNotIn("http://127.0.0.1:8601/health", installer)
        self.assertNotIn("http://127.0.0.1:8502/health", installer)
        self.assertNotIn("/preview/health", installer)


if __name__ == "__main__":
    unittest.main()
