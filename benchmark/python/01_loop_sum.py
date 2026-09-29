def main():
    s = 0
    for i in range(30000000):
        s += (i % 7) * 3
    print(s)
main()
