"""Slug validation and branch naming to avoid directory traversal and D/F collisions."""

import re


def validate_slug(name: str) -> str | None:
    """Validate worktree slug name for security and formatting constraints.

    Returns an error message string if invalid, or None if valid.
    """
    if not name or not name.strip():
        return "name cannot be empty"

    clean_name = name.strip()
    if len(clean_name) > 64:
        return "name too long (max 64 chars)"

    segments = clean_name.split("/")
    for seg in segments:
        if not seg:
            return "name must not contain empty path segments"
        if seg in (".", ".."):
            return "name must not contain '.' or '..'"
        if not re.match(r"^[a-zA-Z0-9._-]+$", seg):
            return f"invalid segment: {seg}"

    return None


def slug_to_branch_name(slug: str) -> str:
    """Convert slug into safe git branch name by replacing nested slashes with '+'."""
    flat = slug.strip().replace("/", "+")
    return f"worktree-{flat}"


def slug_to_dir_name(slug: str) -> str:
    """Convert slug into flat directory name."""
    return slug.strip().replace("/", "+")
