package main

import (
	"fmt"
	"strconv"
	"strings"
)

func main() {
	words, hits, chars := 0, 0, 0
	for i := 0; i < 200000; i++ {
		line := "the quick brown fox " + strconv.Itoa(i) + " jumps over the lazy dog"
		parts := strings.Split(line, " ")
		words += len(parts)
		up := strings.ToUpper(line)
		if strings.Contains(up, "FOX") {
			hits++
		}
		r := strings.ReplaceAll(line, "o", "0")
		chars += len(r)
	}
	fmt.Println(words, hits, chars)
}
