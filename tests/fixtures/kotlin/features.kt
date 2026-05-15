package com.example.demo;

import kotlin.collections.List;

expect class Platform() {
    val name: String;
    fun greet(): String;
};

actual class Platform {
    actual val name: String = "JVM";
    actual fun greet(): String { return "Hello from " + name; };
};

class Point(val x: Int, val y: Int) {
    fun distance(other: Point): Double {
        val dx = (x - other.x).toDouble();
        val dy = (y - other.y).toDouble();
        return dx * dx + dy * dy;
    };
};

sealed class Shape {
    class Circle(val radius: Double): Shape();
    class Square(val side: Double): Shape();
};

fun describe(s: Shape): String {
    return when (s) {
        is Shape.Circle -> "circle";
        is Shape.Square -> "square";
    };
};

inline fun <reified T> isOfType(x: Any?): Boolean {
    return x is T;
};

class Repository {
    private val items: List<Point> = listOf();

    fun add(p: Point): Repository {
        return this;
    };

    @OptIn(ExperimentalStdlibApi::class)
    fun process() {
        for (i in 0..10) {
            for (j in 0..10) {
                if (i == j) { break; };
            };
        };
    };
};
