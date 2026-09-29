package main

import "fmt"

func main() {
	var l []int32
	for i := int32(0); i < 3000000; i++ {
		l = append(l, i%1000)
	}
	var total int64
	for len(l) > 0 {
		total += int64(l[len(l)-1])
		l = l[:len(l)-1]
	}
	fmt.Println(total)
}
