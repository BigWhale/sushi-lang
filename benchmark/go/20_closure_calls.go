package main

import "fmt"

func applyAll(f func(int32) int32, n int32) int64 {
	var total int64
	for i := int32(0); i < n; i++ {
		total += int64(f(i))
	}
	return total
}

func main() {
	k := int32(3)
	f := func(x int32) int32 { return (x%100)*k + 1 }
	fmt.Println(applyAll(f, 10000000))
}
