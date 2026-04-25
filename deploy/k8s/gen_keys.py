import secrets

def generate_api_keys(n: int = 50) -> list[str]:
    """
    Generate n API keys as 32-byte URL-safe Base64 tokens.
    Each key is secrets.token_urlsafe(32), which encodes 32 random bytes.
    """
    return [secrets.token_urlsafe(32) for _ in range(n)]

if __name__ == "__main__":
    keys = generate_api_keys(50)
    print("\n".join(keys))          # one key per line
    # Or, if you prefer one per line:
    # print("\n".join(keys))