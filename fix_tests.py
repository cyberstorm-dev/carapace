import glob

replacements = {
    "from carapace import bootstrap_phase_issue": "from carapace.cli import bootstrap_phase_issue",
    "from carapace import cycle_time": "from carapace.cli import cycle_time",
    "from carapace import justification_backfill": "from carapace.cli import justification_backfill",
    "from carapace import pr_issue_ref": "from carapace.cli import pr_issue_ref",
    "from carapace import reviewer_metrics": "from carapace.cli import reviewer_metrics",
    "from carapace.cli.main import (": "from carapace.cli.gt import (",
    "from carapace.cli.main import DEFAULT_GITEA_URL": "from carapace.cli.gt import DEFAULT_GITEA_URL",
    "from carapace.cli.main import GiteaClient": "from carapace.cli.gt import GiteaClient",
    "from carapace.cli.main import build_graph": "from carapace.cli.gt import build_graph",
    "from carapace.cli.main import load_issues": "from carapace.cli.gt import load_issues",
    "from carapace.cli.main import": "from carapace.cli.gt import",
}

for filepath in glob.glob("tests/**/*.py", recursive=True) + glob.glob("carapace/validator/**/*.py", recursive=True):
    with open(filepath, "r") as f:
        content = f.read()
    
    new_content = content
    for old, new in replacements.items():
        new_content = new_content.replace(old, new)
        
    if new_content != content:
        with open(filepath, "w") as f:
            f.write(new_content)
