import Foundation

@MainActor
actor Counter {
    private var value: Int = 0

    func increment() async throws -> Int {
        value += 1
        return value
    }
}

protocol Drawable {
    associatedtype Color
    func draw(in color: Color) -> Bool
}

class Animal {
    let name: String

    init(name: String) {
        self.name = name
    }

    func speak() -> String {
        return "generic animal sound"
    }
}

class Dog: Animal {
    override func speak() -> String {
        return "woof"
    }
}

enum Direction {
    case north
    case south
    case east(Int)
    case west(magnitude: Double)
}

let numbers = [1, 2, 3, 4, 5]
let doubled = numbers.map { $0 * 2 }
let dict: [String: Int] = ["one": 1, "two": 2]

if let first = numbers.first, first > 0 {
    print("got \(first)")
}

switch numbers.count {
case 0:
    print("empty")
case 1...3:
    print("few")
case let n where n > 100:
    print("many")
default:
    print("some")
}

for element in numbers {
    print(element)
}
