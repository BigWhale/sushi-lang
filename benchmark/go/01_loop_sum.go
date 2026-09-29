package main

import "fmt"

func main() {
	var s int64
	for i := int32(0); i < 30000000; i++ {
		s += int64((i % 7) * 3)
	}
	fmt.Println(s)
}
