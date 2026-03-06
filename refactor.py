import os
import glob

cli_files = [
    "gt.py",
    "cli.py",
    "cycle_time.py",
    "cycle_time_report.py",
    "reviewer_metrics.py",
    "ci_metrics.py",
    "pipeline_metrics.py",
    "task_timeline.py",
    "pr_issue_ref.py",
    "bootstrap_phase_issue.py",
    "composition_report.py",
    "justification_backfill.py"
]

core_files = [
    "queue.py",
    "scheduler.py"
]

all_moves = {}
for f in cli_files:
    if f == "cli.py":
        all_moves[f] = "cli/main.py"
    else:
        all_moves[f] = f"cli/{f}"
for f in core_files:
    all_moves[f] = f"core/{f}"

# Update imports in all files in carapace/ and tests/
for filepath in glob.glob("carapace/**/*.py", recursive=True) + glob.glob("tests/**/*.py", recursive=True):
    if not os.path.isfile(filepath): continue
    with open(filepath, "r") as f:
        content = f.read()
    
    new_content = content
    # Just a simple string replacement script for common import patterns.
    # Note: Using absolute imports (e.g. `from carapace.core.queue import ...`) is safer.
    
    # Replacing absolute imports:
    for old, new in all_moves.items():
        old_mod = old[:-3]
        new_mod = new[:-3].replace('/', '.')
        
        # from carapace.old_mod import
        new_content = new_content.replace(f"from carapace.{old_mod} import", f"from carapace.{new_mod} import")
        # import carapace.old_mod
        new_content = new_content.replace(f"import carapace.{old_mod}", f"import carapace.{new_mod}")
        # from .old_mod import
        new_content = new_content.replace(f"from .{old_mod} import", f"from carapace.{new_mod} import")
        
        # For hateoas, change `from .hateoas` or `from . import hateoas` to `from carapace.hateoas`
        new_content = new_content.replace("from .hateoas import", "from carapace.hateoas import")
        new_content = new_content.replace("from . import hateoas", "import carapace.hateoas as hateoas")

    if new_content != content:
        with open(filepath, "w") as f:
            f.write(new_content)

# Move the files
for old, new in all_moves.items():
    old_path = f"carapace/{old}"
    new_path = f"carapace/{new}"
    if os.path.exists(old_path):
        os.rename(old_path, new_path)

