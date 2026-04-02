"""Manage calendar subscribers per course.

Usage:
    python manage.py add <class> <email>
    python manage.py remove <class> <email>
    python manage.py remove-all <email>
    python manage.py list [<class>]

Use "*" as the class name to subscribe to all courses.
"""

import json
import os
import sys

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(__file__), "subscribers.json")


def load():
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    return {}


def save(data):
    # Keep "*" first in the JSON output
    ordered = {}
    if "*" in data:
        ordered["*"] = data["*"]
    for key in sorted(data):
        if key != "*":
            ordered[key] = data[key]
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(ordered, f, indent=2)
        f.write("\n")


def _find_email(emails, email):
    """Find an email in a list case-insensitively. Returns the stored form or None."""
    for e in emails:
        if e.lower() == email.lower():
            return e
    return None


def add(course, email):
    data = load()
    emails = data.get(course, [])
    if _find_email(emails, email):
        print(f"{email} is already subscribed to {course}")
        return

    if course != "*" and _find_email(data.get("*", []), email):
        print(f"{email} is already subscribed to all courses via \"*\", no need to add to {course}")
        return

    emails.append(email)
    data[course] = sorted(emails, key=str.lower)

    # Adding to * makes course-specific entries redundant — clean them up
    if course == "*":
        removed_from = []
        for key in list(data):
            if key == "*":
                continue
            existing = _find_email(data[key], email)
            if existing:
                data[key].remove(existing)
                if not data[key]:
                    del data[key]
                removed_from.append(key)
        if removed_from:
            print(f"Removed {email} from {', '.join(removed_from)} (now covered by \"*\")")

    save(data)
    print(f"Added {email} to {course}")


def remove(course, email):
    data = load()
    emails = data.get(course, [])
    existing = _find_email(emails, email)
    if not existing:
        print(f"{email} is not subscribed to {course}")
        return
    emails.remove(existing)
    if not emails:
        del data[course]
    else:
        data[course] = emails
    save(data)
    print(f"Removed {email} from {course}")

    # Warn if still covered by *
    if course != "*" and _find_email(data.get("*", []), email):
        print(f"Note: {email} is still subscribed to all courses via \"*\"")


def remove_all(email):
    data = load()
    removed_from = []
    for key in list(data):
        existing = _find_email(data[key], email)
        if existing:
            data[key].remove(existing)
            if not data[key]:
                del data[key]
            removed_from.append(key)
    if removed_from:
        save(data)
        print(f"Removed {email} from {', '.join(removed_from)}")
    else:
        print(f"{email} is not subscribed to any courses")


def list_subs(course=None):
    data = load()
    if not data:
        print("No subscribers")
        return
    if course:
        emails = data.get(course, [])
        if emails:
            for e in emails:
                print(f"  {e}")
        else:
            print(f"No subscribers for {course}")
    else:
        for c in sorted(data):
            print(f"{c}:")
            for e in data[c]:
                print(f"  {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add" and len(sys.argv) == 4:
        add(sys.argv[2], sys.argv[3])
    elif cmd == "remove-all" and len(sys.argv) == 3:
        remove_all(sys.argv[2])
    elif cmd == "remove" and len(sys.argv) == 4:
        remove(sys.argv[2], sys.argv[3])
    elif cmd == "list":
        list_subs(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
