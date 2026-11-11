// hello: Rust's std over WASI preview1, built by rustc for wasm32-wasip1 as
// any program is (tools/wasm_cart.py).
use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    println!("hello from rust, {} args", args.len());
    for a in args.iter().skip(1) {
        println!("{}", a);
    }
}
