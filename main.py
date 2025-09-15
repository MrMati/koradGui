from ui import KoradGui
import os
import os


def main():
  os.environ["RTSSHooksCompatibility"] = "0"
  KoradGui().start()


if __name__ == "__main__":
  main()
