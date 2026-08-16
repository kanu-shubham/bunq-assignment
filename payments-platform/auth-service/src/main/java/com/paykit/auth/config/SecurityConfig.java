package com.paykit.auth.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;

/**
 * Spring Security for a service that sits <em>behind</em> the gateway.
 *
 * <p>THE SHAPE OF THE DECISION — authentication happens once, at the edge. Re-checking the
 * credential here would mean either the raw key travels further into the network than it must,
 * or every service grows its own copy of the auth logic. So this chain permits requests and
 * relies on two things being true: the service is not routable from the internet, and the
 * gateway strips client-supplied identity headers (see {@code AuthenticationFilter}).
 *
 * <p>WHAT IS STILL TURNED OFF, AND WHY
 * <ul>
 *   <li><b>CSRF</b> — a cross-site request forgery needs an ambient credential the browser
 *       attaches automatically, i.e. a cookie. This API authenticates with an
 *       {@code Authorization} header that no browser sends on its own, so there is nothing to
 *       forge. Disabling CSRF on a <em>cookie</em>-authenticated app would be a serious bug.</li>
 *   <li><b>Sessions</b> — STATELESS. No {@code JSESSIONID}, so any instance can serve any
 *       request and horizontal scaling needs no sticky sessions or session replication.</li>
 *   <li><b>HTTP Basic / form login</b> — no login page or browser prompt on a machine API.</li>
 * </ul>
 */
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http
                .csrf(csrf -> csrf.disable())
                .httpBasic(basic -> basic.disable())
                .formLogin(form -> form.disable())
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll())
                .headers(headers -> headers
                        .frameOptions(frame -> frame.deny())
                        .contentTypeOptions(opts -> {}))
                .build();
    }

    /**
     * BCrypt for API-key secrets.
     *
     * <p>The cost factor is configurable so that the test suite can drop to 4 and stay fast
     * while production runs at 10+. Hard-coding it is how a test suite ends up spending
     * eleven minutes on bcrypt.
     */
    @Bean
    public PasswordEncoder passwordEncoder(AuthProperties properties) {
        return new BCryptPasswordEncoder(properties.bcryptStrength());
    }
}
