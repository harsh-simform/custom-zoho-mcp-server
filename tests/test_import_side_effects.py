import subprocess
import sys


def test_importing_core_modules_does_not_read_dotenv_into_the_environment(tmp_path):
    dotenv = tmp_path / "poison.env"
    dotenv.write_text("ZOHO_CLIENT_ID=leaked-at-import\n")
    script = (
        "import os\n"
        "import zoho_projects_axi.core.config, zoho_projects_axi.core.auth, zoho_projects_axi.core.status, zoho_projects_axi.core.errors\n"
        "import zoho_projects_axi.core.gateway, zoho_projects_axi.core.resources, zoho_projects_axi.core.limiter, zoho_projects_axi.core.transport\n"
        "import sys\n"
        "assert 'requests' not in sys.modules, 'the HTTP library must load lazily'\n"
        "print(os.environ.get('ZOHO_CLIENT_ID', 'absent'))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", script],
        env={"ZOHO_MCP_ENV_FILE": str(dotenv), "HOME": str(tmp_path), "PATH": ""},
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    # -I isolates from the cwd; the package is importable because it is installed in the venv.
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "absent"
    assert list(tmp_path.iterdir()) == [dotenv]  # and nothing was written
