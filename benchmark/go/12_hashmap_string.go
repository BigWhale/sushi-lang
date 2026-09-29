package main

import (
	"fmt"
	"strconv"
)

func main() {
	n := 200000
	m := make(map[string]int32)
	for i := 0; i < n; i++ {
		m["key"+strconv.Itoa(i)] = int32(i)
	}
	var total int64
	for i := 0; i < n; i++ {
		if _, ok := m["key"+strconv.Itoa(i*3)]; ok {
			total++
		}
	}
	fmt.Println(len(m), total)
}
