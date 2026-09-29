package main

import (
	"fmt"
	"strconv"
)

func main() {
	s := ""
	for i := 0; i < 50000; i++ {
		s = s + strconv.Itoa(i) + ","
	}
	fmt.Println(len(s))
}
