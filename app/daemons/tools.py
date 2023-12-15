from os import environ

def load_env(env_file):
    try:
        with open(env_file) as f:
            for line in f:
                # Ignore empty lines or lines that start with #
                if line.startswith('#') or not line.strip(): continue
                # Load to local environ
                key, value = line.strip().split('=', 1)
                environ[key] = value

    except FileNotFoundError:
        print('.env file not found')
        return False
    else:
        return True