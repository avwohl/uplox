# plox C++ backend

The C++ backend emits a `.hpp` / `.cpp` pair per grammar in a per-grammar
namespace `plox::<grammar>`. The mechanics mirror the C backend (see
[`c_backend.md`](c_backend.md)) but the surface is idiomatic C++17:
RAII lifetimes, `std::function` callbacks, `std::string_view` lexemes.

## Public API

For a grammar named `calc`:

```cpp
namespace plox::calc {

struct Node {
    bool             is_terminal = false;
    int              kind = 0;
    int              production = -1;
    int              line = 0;
    int              column = 0;
    std::string_view text;        // valid for terminals; into the input buffer
    std::vector<Node*> children;  // observer ptrs; non-owning
};

class Parser {
public:
    explicit Parser(std::string_view input);
    ~Parser();

    Parser(const Parser&)            = delete;
    Parser& operator=(const Parser&) = delete;
    Parser(Parser&&)                 = default;
    Parser& operator=(Parser&&)      = default;

    bool parse();                              // true on success
    const Node*        root()  const noexcept; // tree owned by Parser
    const std::string& error() const noexcept;

    static const char* token_name(int kind);
    static const char* nt_name(int kind);

    /* Lexer feedback (1.1.0+). Same contract as the C backend. */
    using TokenFilter = std::function<int(Parser& parser,
                                          int la_kind,
                                          std::string_view la_text)>;
    void set_token_filter(TokenFilter f);

    using PostReduce  = std::function<void(Parser& parser,
                                           int prod_index,
                                           Node* node)>;
    void set_post_reduce(PostReduce f);
};

}  // namespace plox::calc
```

## Re-entrancy

Same guarantee as the C backend: every parser instance keeps state on
its own object, generated names are namespaced. Two grammars built with
plox link into the same binary cleanly. The pImpl pattern hides table
storage and helper types from the public header so two grammars with
overlapping internal names don't collide at the linker.

## Ownership

The Parser owns every `Node` allocated during a parse. Children pointers
in `Node::children` are non-owning observer pointers into the same
arena. Destroy the Parser to free the tree.

## Balanced-bracket tokens (since 1.3.0)

Tokens declared with `%balanced='<close>'` in the grammar source come
through to the emitted scanner via a per-grammar `kTokenBalanced[]`
constexpr array (one entry per token, holding the close-delimiter byte
or 0). After the DFA matches a balanced token, `next_token()` extends
the match by counting nested open/close pairs in the input until depth
returns to zero. An unmatched close surfaces as a parse error through
the same path as a lexical error. Same shape as the C backend's
`plox_<g>_token_balanced[]`.

## Worked example

[`tests/test_gen_cpp.py::test_cpp_typedef_name_hack_end_to_end`](../tests/test_gen_cpp.py)
implements the full typedef-name hack with `std::function` closures
capturing a shared typedef set. It's the closest analogue to the C
worked example.

## Status of out-of-scope items

Same as the C backend: no semantic-action injection beyond the
post-reduce hook, no error recovery in the driver itself. Use the
parse tree.
