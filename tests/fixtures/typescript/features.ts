import { useState, type Dispatch } from "react";
import type { Config } from "./config";

interface User {
    name: string;
    age?: number;
    readonly id: string;
    greet(prefix?: string): string;
}

type Maybe<T> = T | null | undefined;
type Pair<K, V> = [K, V];

const enum Status {
    Active = "active",
    Inactive = "inactive",
}

abstract class Animal {
    readonly id: string = "";
    protected name: string;
    public age: number = 0;

    constructor(name: string, age: number = 0) {
        this.name = name;
        this.age = age;
    }

    abstract speak(): string;

    greet(prefix?: string): string {
        return (prefix ?? "Hi") + " " + this.name;
    }
}

function identity<T>(x: T): T {
    return x;
}

const numbers: number[] = [1, 2, 3];
const doubled = numbers.map((n: number) => n * 2);
const result = (numbers as readonly number[])[0]!;

namespace App {
    export const VERSION = "1.0";
    export interface Settings {
        debug: boolean;
    }
}

declare global {
    interface Window {
        myProperty: string;
    }
}
