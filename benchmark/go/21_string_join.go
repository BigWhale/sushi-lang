package main

import (
	"fmt"
	"strconv"
	"strings"
)

func main() {
	var parts []string
	for i := 0; i < 50000; i++ {
		parts = append(parts, strconv.Itoa(i)+",")
	}
	s := strings.Join(parts, "")
	fmt.Println(len(s))
}
