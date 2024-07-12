use std::sync::{Arc, Mutex};
use std::thread;

#[derive(Debug)]
struct A {
    value: i32,
}

fn main() {
    // Create an Arc containing a Mutex that wraps an instance of A
    let shared_a = Arc::new(Mutex::new(A { value: 0 }));

    // Vector to hold the handles of the spawned threads
    let mut handles = vec![];

    // Spawn multiple threads
    for i in 0..10 {
        // Clone the Arc to share ownership with the new thread
        let shared_a = Arc::clone(&shared_a);

        // Spawn a new thread
        let handle = thread::spawn(move || {
            // Lock the mutex to get a MutexGuard
            let mut a = shared_a.lock().unwrap();

            // Replace the entire object with a new instance of A
            *a = A { value: a.value+1 };

            // MutexGuard goes out of scope here, releasing the lock
        });

        // Store the thread handle
        handles.push(handle);
    }

    // Wait for all threads to complete
    for handle in handles {
        handle.join().unwrap();
    }

    // Print the final value of shared_a
    println!("Final value of shared_a: {:?}", *shared_a.lock().unwrap());
}
