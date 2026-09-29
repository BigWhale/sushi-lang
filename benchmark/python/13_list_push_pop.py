def main():
    l = []
    for i in range(3000000):
        l.append(i % 1000)
    total = 0
    while l:
        total += l.pop()
    print(total)
main()
