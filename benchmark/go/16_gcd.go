package main

import "fmt"

func gcd(a, b int32) int32 {
	x, y := a, b
	for y != 0 {
		x, y = y, x%y
	}
	return x
}

func main() {
	var total int64
	for i := int32(1); i < 1200; i++ {
		for j := int32(1); j < 1200; j++ {
			total += int64(gcd(i, j))
		}
	}
	fmt.Println(total)
}
