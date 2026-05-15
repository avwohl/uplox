import { useState } from "react";
import config from "./config.json";

class Counter {
    #count = 0;

    constructor(initial = 0) {
        this.#count = initial;
    }

    static create() {
        return new Counter();
    }

    get value() {
        return this.#count;
    }

    set value(v) {
        this.#count = v;
    }

    increment(n = 1) {
        this.#count += n;
        return this;
    }
}

const c = new Counter(10);
c.increment().increment(2);

const data = { name: "test", items: [1, 2, 3] };
const { name, items: [first, ...rest] } = data;

const doubled = items.map((x) => x * 2);
const filtered = doubled.filter((x) => x > 2);
const sum = filtered.reduce((acc, x) => acc + x, 0);

async function fetchUser(id) {
    try {
        const response = await fetch(`/users/${id}`);
        return await response.json();
    } catch (err) {
        return null;
    }
}

const pipeline = (...fns) => (input) => fns.reduce((acc, fn) => fn(acc), input);
const trim = (s) => s.trim();
const upper = (s) => s.toUpperCase();
const greet = pipeline(trim, upper);
console.log(greet("  hello  "));

export default Counter;
export { fetchUser, greet };
