try:
    from .controller import launch
except ImportError:
    from controller import launch


if __name__ == "__main__":
    raise SystemExit(launch())
