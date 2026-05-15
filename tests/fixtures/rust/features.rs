// Smaller Rust fixture that avoids `use` paths and `#[derive(...)]`
// attributes (both need a host filter to emit IDENT_ATTR etc. inside
// attribute paths — see the rust.uplox header note on the
// lexer-feedback IDENT_ATTR / KW_*_ATTR synthetic terminals).

struct Point {
    x: i32,
    y: i32,
}

impl Point {
    fn new(x: i32, y: i32) -> Self {
        Self { x, y }
    }

    fn distance_squared(&self, other: &Point) -> i32 {
        let dx = self.x - other.x;
        let dy = self.y - other.y;
        dx * dx + dy * dy
    }
}

trait Drawable {
    fn draw(&self);
}

impl Drawable for Point {
    fn draw(&self) {
        println!("Point");
    }
}

enum Shape {
    Circle { radius: f64 },
    Square(f64),
}

fn describe(s: &Shape) -> &'static str {
    match s {
        Shape::Circle { radius } if *radius > 10.0 => "big circle",
        Shape::Circle { .. } => "small circle",
        Shape::Square(side) if *side < 1.0 => "tiny square",
        Shape::Square(_) => "regular square",
    }
}

fn maybe_value(flag: bool) -> Option<i32> {
    if flag {
        Some(42)
    } else {
        None
    }
}

fn main() {
    let p1 = Point::new(0, 0);
    let p2 = Point::new(3, 4);
    let d = p1.distance_squared(&p2);
    println!("d^2 = {}", d);

    let s = Shape::Circle { radius: 5.0 };
    println!("{}", describe(&s));
}
