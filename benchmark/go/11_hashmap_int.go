package main

import "fmt"

func main() {
	n := int32(500000)
	m := make(map[int32]int32)
	for i := int32(0); i < n; i++ {
		m[i*7] = i
	}
	var total int64
	for i := int32(0); i < n*2; i++ {
		if v, ok := m[i*7]; ok {
			total += int64(v)
		}
	}
	fmt.Println(len(m), total)
}
