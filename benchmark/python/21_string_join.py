def main():
    parts = []
    for i in range(50000):
        parts.append(f"{i},")
    s = "".join(parts)
    print(len(s))
main()
