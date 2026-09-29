def main():
    n = 200000
    m = {}
    for i in range(n):
        m[f"key{i}"] = i
    total = 0
    for i in range(n):
        if f"key{i * 3}" in m:
            total += 1
    print(f"{len(m)} {total}")
main()
