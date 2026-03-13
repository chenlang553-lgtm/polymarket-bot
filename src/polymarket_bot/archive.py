import json
import os


class WindowArchiveWriter:
    def __init__(self, path):
        self.path = path

    def write(self, record):
        directory = os.path.dirname(self.path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        with open(self.path, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


class JsonlWriter:
    def __init__(self, path):
        self.path = path

    def write(self, record):
        directory = os.path.dirname(self.path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        with open(self.path, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


def load_window_records(path):
    records = []
    if not os.path.exists(path):
        return records
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records
