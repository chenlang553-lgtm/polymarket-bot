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
