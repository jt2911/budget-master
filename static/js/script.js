/* ==========================================
   BUDGET MASTER - JAVASCRIPT
   Animations & Interactions
   ========================================== */

document.addEventListener('DOMContentLoaded', function() {
    
    // ========== Loading Screen ==========
    const loader = document.getElementById('loader');
    if (loader) {
        setTimeout(() => {
            loader.classList.add('hidden');
        }, 1800);
    }
    
    // ========== Scroll Reveal Animation ==========
    const revealElements = document.querySelectorAll('.reveal');
    
    const revealOnScroll = () => {
        revealElements.forEach(el => {
            const elementTop = el.getBoundingClientRect().top;
            const elementVisible = 150;
            
            if (elementTop < window.innerHeight - elementVisible) {
                el.classList.add('active');
            }
        });
    };
    
    window.addEventListener('scroll', revealOnScroll);
    revealOnScroll(); // Initial check
    
    // ========== Navbar Scroll Effect ==========
    const navbar = document.querySelector('.navbar');
    
    if (navbar) {
        window.addEventListener('scroll', () => {
            if (window.scrollY > 50) {
                navbar.classList.add('scrolled');
            } else {
                navbar.classList.remove('scrolled');
            }
        });
    }
    
    // ========== Mobile Navigation Toggle ==========
    const navToggle = document.getElementById('navToggle');
    const navMenu = document.getElementById('navMenu');
    
    if (navToggle && navMenu) {
        navToggle.addEventListener('click', () => {
            navMenu.classList.toggle('active');
            navToggle.classList.toggle('active');
        });
        
        // Close menu when clicking a link
        navMenu.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', () => {
                navMenu.classList.remove('active');
                navToggle.classList.remove('active');
            });
        });
    }
    
    // ========== Password Toggle ==========
    const toggleButtons = document.querySelectorAll('.toggle-password');
    
    toggleButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const input = btn.parentElement.querySelector('input');
            const icon = btn.querySelector('i');
            
            if (input.type === 'password') {
                input.type = 'text';
                icon.classList.remove('fa-eye');
                icon.classList.add('fa-eye-slash');
            } else {
                input.type = 'password';
                icon.classList.remove('fa-eye-slash');
                icon.classList.add('fa-eye');
            }
        });
    });
    
    // ========== Password Strength Indicator ==========
    const passwordInput = document.getElementById('password');
    const strengthIndicator = document.getElementById('passwordStrength');
    
    if (passwordInput && strengthIndicator) {
        passwordInput.addEventListener('input', (e) => {
            const password = e.target.value;
            let strength = 0;
            let feedback = '';
            
            if (password.length >= 8) strength++;
            if (password.match(/[a-z]/)) strength++;
            if (password.match(/[A-Z]/)) strength++;
            if (password.match(/[0-9]/)) strength++;
            if (password.match(/[^a-zA-Z0-9]/)) strength++;
            
            const colors = ['#ef4444', '#f59e0b', '#eab308', '#84cc16', '#10b981'];
            const labels = ['Very Weak', 'Weak', 'Fair', 'Strong', 'Very Strong'];
            
            if (password.length > 0) {
                strengthIndicator.innerHTML = `
                    <div style="display: flex; gap: 4px; margin-top: 8px;">
                        ${[0,1,2,3,4].map(i => `
                            <div style="flex: 1; height: 4px; border-radius: 2px; 
                                        background: ${i < strength ? colors[strength-1] : '#e2e8f0'};">
                            </div>
                        `).join('')}
                    </div>
                    <span style="font-size: 12px; color: ${colors[strength-1]}; margin-top: 4px; display: block;">
                        ${labels[strength-1] || ''}
                    </span>
                `;
            } else {
                strengthIndicator.innerHTML = '';
            }
        });
    }
    
    // ========== Flash Message Auto-dismiss ==========
    const flashMessages = document.querySelectorAll('.flash-message');
    
    flashMessages.forEach(message => {
        // Auto dismiss after 5 seconds
        setTimeout(() => {
            message.style.animation = 'slideOut 0.3s ease forwards';
            setTimeout(() => message.remove(), 300);
        }, 5000);
        
        // Manual dismiss
        const closeBtn = message.querySelector('.flash-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                message.style.animation = 'slideOut 0.3s ease forwards';
                setTimeout(() => message.remove(), 300);
            });
        }
    });
    
    // ========== Smooth Scroll for Anchor Links ==========
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId === '#') return;
            
            const targetElement = document.querySelector(targetId);
            if (targetElement) {
                e.preventDefault();
                targetElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
    
    // ========== Animated Particles (Hero) ==========
    const particlesContainer = document.getElementById('particles');
    
    if (particlesContainer) {
        for (let i = 0; i < 50; i++) {
            const particle = document.createElement('div');
            particle.style.cssText = `
                position: absolute;
                width: ${Math.random() * 4 + 1}px;
                height: ${Math.random() * 4 + 1}px;
                background: rgba(255, 255, 255, ${Math.random() * 0.3 + 0.1});
                border-radius: 50%;
                left: ${Math.random() * 100}%;
                top: ${Math.random() * 100}%;
                animation: float ${Math.random() * 10 + 10}s linear infinite;
            `;
            particlesContainer.appendChild(particle);
        }
    }
    
    // ========== Form Validation Visual Feedback ==========
    const forms = document.querySelectorAll('form');
    
    forms.forEach(form => {
        const inputs = form.querySelectorAll('input, select');
        
        inputs.forEach(input => {
            input.addEventListener('blur', () => {
                if (input.hasAttribute('required') && !input.value) {
                    input.style.borderColor = '#ef4444';
                } else {
                    input.style.borderColor = '';
                }
            });
            
            input.addEventListener('input', () => {
                input.style.borderColor = '';
            });
        });
    });
    
    // ========== Add CSS Animation for Flash Messages ==========
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideOut {
            to {
                transform: translateX(120%);
                opacity: 0;
            }
        }
    `;
    document.head.appendChild(style);
    
    // ========== Tooltip Initialization (if needed) ==========
    const tooltips = document.querySelectorAll('[data-tooltip]');
    
    tooltips.forEach(el => {
        el.style.position = 'relative';
        
        el.addEventListener('mouseenter', () => {
            const tooltip = document.createElement('div');
            tooltip.className = 'tooltip';
            tooltip.textContent = el.getAttribute('data-tooltip');
            tooltip.style.cssText = `
                position: absolute;
                bottom: 100%;
                left: 50%;
                transform: translateX(-50%);
                padding: 8px 12px;
                background: #1e293b;
                color: white;
                font-size: 12px;
                border-radius: 6px;
                white-space: nowrap;
                z-index: 1000;
                margin-bottom: 8px;
            `;
            el.appendChild(tooltip);
        });
        
        el.addEventListener('mouseleave', () => {
            const tooltip = el.querySelector('.tooltip');
            if (tooltip) tooltip.remove();
        });
    });
    
    // ========== Chart Color Variables ==========
    window.chartColors = {
        primary: '#6366f1',
        secondary: '#10b981',
        accent: '#f59e0b',
        danger: '#ef4444',
        purple: '#8b5cf6',
        cyan: '#06b6d4',
        lime: '#84cc16',
        pink: '#ec4899'
    };
    
    console.log('✅ Budget Master initialized successfully!');
});
