package main;

import (
    "fmt";
);

type Point struct {
    X int;
    Y int;
};

type Shape interface {
    Area() float64;
    Perimeter() float64;
};

func add(a int, b int) int {
    return a + b;
};

func sum(nums ...int) int {
    total := 0;
    for _, n := range (nums) {
        total = total + n;
    };
    return total;
};

func main() {
    p := Point{X: 1, Y: 2};
    fmt.Println(p);
    if (1 > 0) {
        fmt.Println("yes");
    } else {
        fmt.Println("no");
    };
    nums := []int{1, 2, 3, 4, 5};
    s := sum(nums...);
    fmt.Printf("sum = %d", s);

    switch (s) {
    case 15:
        fmt.Println("fifteen");
    default:
        fmt.Println("other");
    };

    var x int = 10;
    var y, z int;
    _ = x;
    _ = y;
    _ = z;
};
