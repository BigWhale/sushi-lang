def main():
    words = hits = chars = 0
    for i in range(200000):
        line = f"the quick brown fox {i} jumps over the lazy dog"
        parts = line.split(" ")
        words += len(parts)
        up = line.upper()
        if "FOX" in up:
            hits += 1
        r = line.replace("o", "0")
        chars += len(r)
    print(f"{words} {hits} {chars}")
main()
