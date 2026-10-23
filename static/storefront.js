(() => {
    "use strict";

    const config = document.body.dataset;
    const products = JSON.parse(config.products || "[]");
    const productBySku = new Map(products.map(product => [product.sku, product]));
    const cart = new Map();
    const drawerLayer = document.getElementById("cart-drawer");
    const drawer = drawerLayer.querySelector(".drawer");
    const cartItems = document.getElementById("cart-items");
    const cartTotal = document.getElementById("cart-total");
    const cartCount = document.getElementById("cart-count");
    const shippingCopy = document.getElementById("shipping-copy");
    const shippingFill = document.getElementById("shipping-fill");
    const progressBar = shippingFill.parentElement;
    const shippingQualification = document.getElementById("shipping-qualification");
    const checkoutStatus = document.getElementById("checkout-status");
    const money = cents => `$${(cents / 100).toFixed(2)}`;
    const totalCents = () => [...cart.values()].reduce(
        (sum, item) => sum + item.product.price_cents * item.quantity,
        0
    );
    const orderItems = () => [...cart.values()].map(
        item => ({ sku: item.product.sku, quantity: item.quantity })
    );
    let returnFocus = null;
    let stripeClient = null;
    let stripeElements = null;
    let stripeIntent = null;

    function setStatus(message, isError = false) {
        checkoutStatus.textContent = message;
        checkoutStatus.style.color = isError ? "#f1a28b" : "";
    }

    function resetStripeCheckout() {
        if (!stripeIntent) return;
        const stripeOptions = document.getElementById("stripe-payment-options");
        const stripeStart = document.getElementById("stripe-start");
        if (stripeOptions && stripeStart) {
            stripeOptions.hidden = true;
            stripeOptions.querySelector("#stripe-express-checkout").replaceChildren();
            stripeOptions.querySelector("#stripe-payment-element").replaceChildren();
            stripeStart.hidden = false;
            stripeStart.disabled = false;
        }
        stripeElements = null;
        stripeIntent = null;
    }

    function setQuantity(sku, quantity) {
        if (quantity <= 0) cart.delete(sku);
        else cart.set(sku, { product: productBySku.get(sku), quantity });
        resetStripeCheckout();
        renderCart();
    }

    function addProduct(sku) {
        const product = productBySku.get(sku);
        if (!product || !product.available) {
            setStatus("This product is not currently available.", true);
            return;
        }
        const current = cart.get(sku)?.quantity || 0;
        if (current >= 10) {
            setStatus("Maximum quantity per product is 10.", true);
            return;
        }
        cart.set(sku, { product, quantity: current + 1 });
        resetStripeCheckout();
        renderCart();
        openDrawer();
    }

    function renderCart() {
        const entries = [...cart.entries()];
        const count = entries.reduce((sum, [, item]) => sum + item.quantity, 0);
        const total = totalCents();
        cartCount.textContent = String(count);
        cartTotal.textContent = `${money(total)} USD`;
        const percentage = Math.min(100, total / 3000 * 100);
        shippingFill.style.width = `${percentage}%`;
        progressBar.setAttribute("aria-valuenow", String(Math.min(30, total / 100)));
        shippingCopy.textContent = total >= 3000
            ? "Complimentary shipping unlocked."
            : `Add ${money(3000 - total)} USD for complimentary shipping.`;
        shippingQualification.textContent = total >= 3000
            ? "Your subtotal qualifies for complimentary shipping."
            : "Shipping is confirmed with the concierge.";
        cartItems.replaceChildren();
        if (!entries.length) {
            const empty = document.createElement("p");
            empty.className = "cart-empty";
            empty.textContent = "Your bag is waiting for a ritual.";
            cartItems.append(empty);
        }
        entries.forEach(([sku, item]) => {
            const row = document.createElement("article");
            row.className = "cart-row";
            const description = document.createElement("div");
            const title = document.createElement("h3");
            title.textContent = item.product.name;
            const price = document.createElement("p");
            price.textContent = `${money(item.product.price_cents)} each · ${money(item.product.price_cents * item.quantity)} USD`;
            description.append(title, price);
            const controls = document.createElement("div");
            controls.className = "cart-row-controls";
            const decrease = document.createElement("button");
            decrease.className = "quantity-button";
            decrease.type = "button";
            decrease.textContent = "−";
            decrease.setAttribute("aria-label", `Remove one ${item.product.name}`);
            decrease.addEventListener("click", () => setQuantity(sku, item.quantity - 1));
            const quantity = document.createElement("span");
            quantity.className = "quantity-value";
            quantity.textContent = String(item.quantity);
            const increase = document.createElement("button");
            increase.className = "quantity-button";
            increase.type = "button";
            increase.textContent = "+";
            increase.setAttribute("aria-label", `Add one ${item.product.name}`);
            increase.disabled = item.quantity >= 10;
            increase.addEventListener("click", () => setQuantity(sku, item.quantity + 1));
            controls.append(decrease, quantity, increase);
            const remove = document.createElement("button");
            remove.className = "remove-button";
            remove.type = "button";
            remove.textContent = "Remove";
            remove.addEventListener("click", () => setQuantity(sku, 0));
            row.append(description, controls, remove);
            cartItems.append(row);
        });
        setStatus("");
    }

    function openDrawer() {
        returnFocus = document.activeElement;
        drawerLayer.hidden = false;
        document.body.classList.add("drawer-open");
        document.getElementById("bag-close").focus();
    }

    function closeDrawer() {
        drawerLayer.hidden = true;
        document.body.classList.remove("drawer-open");
        if (returnFocus instanceof HTMLElement) returnFocus.focus();
    }

    document.querySelectorAll(".add-to-cart").forEach(button => {
        button.addEventListener("click", () => addProduct(button.dataset.sku));
    });
    document.getElementById("bag-open").addEventListener("click", openDrawer);
    document.getElementById("bag-close").addEventListener("click", closeDrawer);
    document.getElementById("drawer-backdrop").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !drawerLayer.hidden) closeDrawer();
        if (event.key === "Tab" && !drawerLayer.hidden) {
            const focusable = [...drawer.querySelectorAll("button:not(:disabled), a[href], summary")];
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });

    document.querySelectorAll("[data-filter]").forEach(button => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-filter]").forEach(filter => {
                filter.setAttribute("aria-pressed", String(filter === button));
            });
            document.querySelectorAll(".product-card").forEach(card => {
                card.hidden = button.dataset.filter !== "all"
                    && card.dataset.category !== button.dataset.filter;
            });
        });
    });

    const menuButton = document.getElementById("menu-toggle");
    const mobileNavigation = document.getElementById("mobile-navigation");
    menuButton.addEventListener("click", () => {
        const expanded = menuButton.getAttribute("aria-expanded") === "true";
        menuButton.setAttribute("aria-expanded", String(!expanded));
        menuButton.setAttribute("aria-label", expanded ? "Open navigation" : "Close navigation");
        mobileNavigation.classList.toggle("open", !expanded);
    });
    mobileNavigation.querySelectorAll("a").forEach(link => link.addEventListener("click", () => {
        mobileNavigation.classList.remove("open");
        menuButton.setAttribute("aria-expanded", "false");
        menuButton.setAttribute("aria-label", "Open navigation");
    }));

    document.getElementById("whatsapp-checkout").addEventListener("click", () => {
        if (!cart.size) {
            setStatus("Add a product to your bag before checkout.", true);
            return;
        }
        const total = totalCents();
        const lines = [...cart.values()].map(({ product, quantity }) =>
            `${quantity} × ${product.name} — ${money(product.price_cents * quantity)} USD`
        );
        const shipping = total >= 3000 ? "Complimentary shipping unlocked" : "Shipping to be confirmed";
        const message = [
            "Hello Bare Artisan Products, I would like to place an order:",
            "",
            ...lines,
            "",
            `Subtotal: ${money(total)} USD`,
            `Shipping: ${shipping}`
        ].join("\n");
        window.open(
            `https://wa.me/18762874304?text=${encodeURIComponent(message)}`,
            "_blank",
            "noopener,noreferrer"
        );
    });

    if (config.paypalEnabled === "true") {
        let paypalReadyCheck;
        const paypalLoadTimeout = window.setTimeout(() => {
            window.clearInterval(paypalReadyCheck);
            setStatus("PayPal did not load. Please try another checkout option.", true);
        }, 15000);
        paypalReadyCheck = window.setInterval(() => {
            if (!window.paypal) return;
            window.clearInterval(paypalReadyCheck);
            window.clearTimeout(paypalLoadTimeout);
            window.paypal.Buttons({
                style: { layout: "vertical", color: "gold", shape: "rect", label: "paypal" },
                createOrder: async () => {
                    if (!cart.size) throw new Error("Add a product to your bag before checkout.");
                    const response = await fetch("/api/paypal/orders", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ items: orderItems() })
                    });
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.error || "PayPal could not create the order.");
                    return result.id;
                },
                onApprove: async data => {
                    const response = await fetch(
                        `/api/paypal/orders/${encodeURIComponent(data.orderID)}/capture`,
                        {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: "{}"
                        }
                    );
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.error || "PayPal could not capture the payment.");
                    setStatus("PayPal payment completed. Thank you for your order.");
                    cart.clear();
                    resetStripeCheckout();
                    renderCart();
                },
                onError: error => {
                    console.error("PayPal checkout failed.", error);
                    setStatus(error.message || "PayPal checkout failed. Please try again.", true);
                }
            }).render("#paypal-buttons").catch(error => {
                console.error("PayPal buttons could not be loaded.", error);
                setStatus("PayPal buttons could not be loaded. Please try another checkout option.", true);
            });
        }, 100);
    }

    if (config.stripeEnabled === "true") {
        const stripeForm = document.getElementById("stripe-payment-form");
        const stripeStart = document.getElementById("stripe-start");
        const stripeOptions = document.getElementById("stripe-payment-options");
        stripeStart.addEventListener("click", async () => {
            if (!cart.size) {
                setStatus("Add a product to your bag before checkout.", true);
                return;
            }
            stripeStart.disabled = true;
            setStatus("Preparing secure checkout…");
            try {
                const response = await fetch("/api/stripe/payment-intents", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ items: orderItems() })
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || "Stripe could not start checkout.");
                if (!window.Stripe) throw new Error("Stripe.js did not load. Please try again.");
                stripeClient = stripeClient || window.Stripe(config.stripePublishableKey);
                stripeIntent = result;
                stripeElements = stripeClient.elements({ clientSecret: result.client_secret });
                const expressCheckout = stripeElements.create("expressCheckout");
                expressCheckout.mount("#stripe-express-checkout");
                stripeElements.create("payment").mount("#stripe-payment-element");
                stripeOptions.hidden = false;
                stripeStart.hidden = true;
                setStatus("Choose an available express wallet or card option.");
                expressCheckout.on("confirm", async () => {
                    try {
                        const { error: submitError } = await stripeElements.submit();
                        if (submitError) {
                            setStatus(submitError.message, true);
                            return;
                        }
                        const { error } = await stripeClient.confirmPayment({
                            elements: stripeElements,
                            clientSecret: stripeIntent.client_secret,
                            confirmParams: { return_url: window.location.href }
                        });
                        if (error) setStatus(error.message, true);
                    } catch (error) {
                        console.error("Stripe express checkout failed.", error);
                        setStatus(error.message || "Stripe checkout failed. Please try again.", true);
                    }
                });
            } catch (error) {
                stripeStart.disabled = false;
                console.error("Stripe checkout could not be started.", error);
                setStatus(error.message || "Stripe checkout could not be started.", true);
            }
        });
        stripeForm.addEventListener("submit", async event => {
            event.preventDefault();
            if (!stripeClient || !stripeElements || !stripeIntent) return;
            try {
                const { error: submitError } = await stripeElements.submit();
                if (submitError) {
                    setStatus(submitError.message, true);
                    return;
                }
                const { error } = await stripeClient.confirmPayment({
                    elements: stripeElements,
                    clientSecret: stripeIntent.client_secret,
                    confirmParams: { return_url: window.location.href }
                });
                if (error) setStatus(error.message, true);
            } catch (error) {
                console.error("Stripe payment confirmation failed.", error);
                setStatus(error.message || "Stripe payment could not be confirmed.", true);
            }
        });
    }

    renderCart();
})();
