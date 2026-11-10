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
        self.assertTrue({"data", ".env", "**/.env", "narratoai/config.toml*", "narratoai/storage"}.issubset(lines))

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
        for excluded in ("data", ".env", "narratoai/storage", "narratoai/config.toml*"):
            self.assertIn(f"--exclude='{excluded}'", script)
        self.assertIn("curl -sf http://localhost/api/health", script)
        self.assertIn("curl -sf http://127.0.0.1:8601/health", script)
        self.assertIn("for attempt in", script)
        self.assertNotIn("_stcore/health", script)

    def test_container_logs_are_bounded_and_legacy_streamlit_is_absent(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertGreaterEqual(compose.count('max-size: "10m"'), 2)
        self.assertGreaterEqual(compose.count('max-file: "3"'), 2)
        self.assertIn("./data:/app/data", compose)
        self.assertIn("narrato-engine:", compose)
        self.assertIn("narrato-engine-config:", compose)
        self.assertIn("VIDEO_HUB_NARRATO_ENGINE_URL", compose)
        self.assertNotIn("8501", compose)


class HostDeploymentTests(unittest.TestCase):
    def test_host_routes_and_websocket_are_configured(self):
        config = (ROOT / "deploy/nginx-host.conf").read_text()
        for route in ("location /api/", "location /narrato/", "location /preview/"):
            self.assertIn(route, config)
        self.assertIn("$http_upgrade", config)
        self.assertIn("try_files $uri $uri/ /index.html", config)
        self.assertIn("return 301 /narrate", config)
        self.assertNotIn("proxy_pass http://127.0.0.1:8501", config)

    def test_systemd_services_use_shared_asr_without_legacy_streamlit(self):
        backend = (ROOT / "deploy/systemd/video-hub-backend.service").read_text()
        self.assertIn("VIDEO_HUB_ASR_URL=http://127.0.0.1:8600/v1/audio/transcriptions", backend)
        self.assertIn("VIDEO_HUB_NARRATO_TASKS_DIR=/opt/video-hub/narratoai/storage/tasks", backend)
        engine = (ROOT / "deploy/systemd/narrato-engine.service").read_text()
        self.assertIn("NARRATO_ENGINE_HOST=127.0.0.1", engine)
        self.assertIn("NARRATO_ENGINE_PORT=8601", engine)
        self.assertIn("-m engine.server", engine)
        self.assertFalse((ROOT / "deploy/systemd/narratoai.service").exists())

    def test_remote_install_preserves_runtime_data_and_backs_up_configuration(self):
        script = (ROOT / "deploy/remote-install.sh").read_text()
        self.assertIn("/opt/video-hub-backups", script)
        self.assertIn("cp -a /opt/NarratoAI/config.toml", script)
        self.assertIn("cp -a /opt/NarratoAI/storage", script)
        self.assertNotIn("rm -rf /opt/NarratoAI", script)
        self.assertIn("nginx -t", script)
        self.assertIn("migrate_legacy.py", script)
        self.assertIn('chown -R narratoai:narratoai', script)
        self.assertIn('systemctl stop narratoai', script)

    def test_all_writable_narrato_resources_survive_code_sync(self):
        script = (ROOT / "deploy.sh").read_text()
        installer = (ROOT / "deploy/remote-install.sh").read_text()
        for directory in ("videos", "scripts", "bgm", "songs", "srt", "fonts", "safe_areas", "tts_reference_audio"):
            self.assertIn(f"--exclude='narratoai/resource/{directory}'", script)
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


if __name__ == "__main__":
    unittest.main()
