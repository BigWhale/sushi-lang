package main

import "fmt"

func main() {
	x := uint64(88172645463325252)
	var acc uint64
	for i := 0; i < 10000000; i++ {
		x ^= x << 13
		x ^= x >> 7
		x ^= x << 17
		acc += x >> 56
	}
	fmt.Println(acc)
}
