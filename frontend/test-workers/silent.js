// Boots and then says nothing, ever. This is what a Content-Security-Policy
// refusal of the wasm compile actually looks like: no error, no rejection, no
// log. The deadline is the only thing that can catch it.
self.onmessage = () => {};
