"""Manage calendar subscribers per course.

Usage:
    python manage.py add <class> <email>
    python manage.py remove <class> <email>
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
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def add(course, email):
    data = load()
    emails = data.get(course, [])
    if email in emails:
        print(f"{email} is already subscribed to {course}")
        return
    emails.append(email)
    data[course] = sorted(emails, key=str.lower)
    save(data)
    print(f"Added {email} to {course}")


def remove(course, email):
    data = load()
    emails = data.get(course, [])
    if email not in emails:
        print(f"{email} is not subscribed to {course}")
        return
    emails.remove(email)
    if not emails:
        del data[course]
    else:
        data[course] = emails
    save(data)
    print(f"Removed {email} from {course}")


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
    elif cmd == "remove" and len(sys.argv) == 4:
        remove(sys.argv[2], sys.argv[3])
    elif cmd == "list":
        list_subs(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
