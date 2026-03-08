import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from carapace.issue_ref import IssueRef, parse_issue_ref


def test_parse_issue_ref_defaults_local_repo_for_shorthand():
    ref = parse_issue_ref("#123", default_repo="o/r")
    assert isinstance(ref, IssueRef)
    assert ref == IssueRef(repo="o/r", number=123)

