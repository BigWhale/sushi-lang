package main

import "fmt"

func tak(x, y, z int32) int32 {
	if y < x {
		return tak(tak(x-1, y, z), tak(y-1, z, x), tak(z-1, x, y))
	}
	return z
}

func main() {
	fmt.Println(tak(22, 16, 8))
}
