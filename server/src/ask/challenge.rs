//! The proof of work a question must carry: the page asks for a challenge, the browser searches
//! for a solution (about 65,000 SHA-256 hashes on average) and sends it with the question. Only a
//! client that runs the page's script can ask, each question costs that client some computing,
//! and a challenge is valid once, for five minutes, from the address it was issued to.
//!
//! A challenge is `<random>.<expiry>.<mac>`: 16 random bytes and the expiry in Unix seconds, signed
//! with a key made at start-up, over the client's address too. Nothing is stored until a challenge
//! is used; then it is kept until it expires, so it cannot be used again.

use std::collections::{HashSet, VecDeque};
use std::sync::Mutex;

use hmac::{Hmac, KeyInit, Mac};
use sha2::{Digest, Sha256};

/// Leading zero bits the SHA-256 of `<challenge>:<solution>` must have.
pub const BITS: u32 = 16;
/// Seconds a challenge stays valid.
const TTL: u64 = 300;

/// Why a challenge was not accepted.
#[derive(Debug, PartialEq, Eq)]
pub enum Refused {
    Malformed,
    Forged,
    Expired,
    Unsolved,
    Used,
}

pub struct Challenges {
    key: [u8; 32],
    used: Mutex<Used>,
}

/// Used challenges, and the same with their expiry in the order they were used, so that expired
/// ones leave from the front without a scan of the rest.
#[derive(Default)]
struct Used {
    set: HashSet<String>,
    queue: VecDeque<(u64, String)>,
}

impl Challenges {
    pub fn new() -> Self {
        let mut key = [0; 32];
        getrandom::fill(&mut key).expect("the operating system provides random bytes");
        Self {
            key,
            used: Mutex::new(Used::default()),
        }
    }

    fn mac(&self, body: &str, address: &str) -> Hmac<Sha256> {
        let mut mac = <Hmac<Sha256> as KeyInit>::new_from_slice(&self.key).expect("any key");
        mac.update(body.as_bytes());
        mac.update(b"\0");
        mac.update(address.as_bytes());
        mac
    }

    /// A new challenge for `address`, valid from `now` (Unix seconds).
    pub fn issue(&self, address: &str, now: u64) -> String {
        let mut random = [0; 16];
        getrandom::fill(&mut random).expect("the operating system provides random bytes");
        let body = format!("{}.{}", hex(&random), now + TTL);
        let mac = self.mac(&body, address).finalize().into_bytes();
        format!("{body}.{}", hex(&mac))
    }

    /// Accept `challenge` with `solution` from `address` at `now`, once.
    pub fn accept(
        &self,
        challenge: &str,
        solution: &str,
        address: &str,
        now: u64,
    ) -> Result<(), Refused> {
        let (body, mac) = challenge.rsplit_once('.').ok_or(Refused::Malformed)?;
        let expiry: u64 = body
            .split_once('.')
            .and_then(|(_, e)| e.parse().ok())
            .ok_or(Refused::Malformed)?;
        let mac = unhex(mac).ok_or(Refused::Malformed)?;
        self.mac(body, address)
            .verify_slice(&mac)
            .map_err(|_| Refused::Forged)?;
        if now > expiry {
            return Err(Refused::Expired);
        }
        if solution.is_empty()
            || solution.len() > 20
            || !solution.bytes().all(|b| b.is_ascii_digit())
            || leading_zero_bits(&Sha256::digest(format!("{challenge}:{solution}"))) < BITS
        {
            return Err(Refused::Unsolved);
        }
        let mut used = self.used.lock().expect("not poisoned");
        // expiries are not quite in the order of use; one that is not yet at the front leaves later
        while used.queue.front().is_some_and(|(until, _)| *until < now) {
            if let Some((_, gone)) = used.queue.pop_front() {
                used.set.remove(&gone);
            }
        }
        if !used.set.insert(challenge.to_string()) {
            return Err(Refused::Used);
        }
        used.queue.push_back((expiry, challenge.to_string()));
        Ok(())
    }
}

impl Default for Challenges {
    fn default() -> Self {
        Self::new()
    }
}

fn leading_zero_bits(digest: &[u8]) -> u32 {
    let mut bits = 0;
    for byte in digest {
        if *byte == 0 {
            bits += 8;
        } else {
            return bits + byte.leading_zeros();
        }
    }
    bits
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    if !text.len().is_multiple_of(2) {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok())
        .collect()
}

/// A solution for `challenge`, as the page's script finds one. For tests.
pub fn solve(challenge: &str) -> String {
    (0u64..)
        .map(|n| n.to_string())
        .find(|n| leading_zero_bits(&Sha256::digest(format!("{challenge}:{n}"))) >= BITS)
        .expect("a solution exists")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_solved_challenge_is_accepted_once_from_its_address_until_it_expires() {
        let challenges = Challenges::new();
        let challenge = challenges.issue("192.0.2.1", 1000);
        let solution = solve(&challenge);
        assert_eq!(
            challenges.accept(&challenge, &solution, "192.0.2.2", 1000),
            Err(Refused::Forged)
        );
        assert_eq!(
            challenges.accept(&challenge, "1", "192.0.2.1", 1000),
            Err(Refused::Unsolved)
        );
        assert_eq!(
            challenges.accept(&challenge, &solution, "192.0.2.1", 1000 + TTL + 1),
            Err(Refused::Expired)
        );
        assert_eq!(
            challenges.accept(&challenge, &solution, "192.0.2.1", 1010),
            Ok(())
        );
        assert_eq!(
            challenges.accept(&challenge, &solution, "192.0.2.1", 1011),
            Err(Refused::Used)
        );
    }

    #[test]
    fn a_changed_expiry_or_a_challenge_from_another_key_is_refused() {
        let challenges = Challenges::new();
        let challenge = challenges.issue("a", 1000);
        let (random, rest) = challenge.split_once('.').unwrap();
        let (_, mac) = rest.split_once('.').unwrap();
        let later = format!("{random}.{}.{mac}", 1000 + TTL + 10_000);
        assert_eq!(
            challenges.accept(&later, &solve(&later), "a", 1000),
            Err(Refused::Forged)
        );
        let other = Challenges::new().issue("a", 1000);
        assert_eq!(
            challenges.accept(&other, &solve(&other), "a", 1000),
            Err(Refused::Forged)
        );
        assert_eq!(
            challenges.accept("nonsense", "1", "a", 1000),
            Err(Refused::Malformed)
        );
    }
}
