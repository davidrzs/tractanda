//! How many questions a visitor, an address and the whole site may ask, and how fast the server
//! may call the model. Visitors and addresses are counted under keyed hashes whose key changes
//! every day and lives only in memory: no address is kept, and nothing is written to disk. A
//! restart starts the counts afresh. An IPv6 address counts as its /64 network, the block one
//! connection usually has.

use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;

/// Questions per visitor (address and browser) a day.
pub const PER_VISITOR: u32 = 15;
/// Questions per address a day: several browsers behind one address.
pub const PER_ADDRESS: u32 = 30;
/// Questions per address an hour: one address cannot take the site's hour.
pub const PER_ADDRESS_HOUR: usize = 6;
/// Questions for the whole site an hour.
pub const PER_HOUR: usize = 60;

const HOUR: Duration = Duration::from_secs(3600);

/// Which limit a question ran into.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Limit {
    Visitor,
    Address,
    AddressHour,
    Hour,
}

impl Limit {
    pub fn code(self) -> &'static str {
        match self {
            Limit::Visitor => "visitor",
            Limit::Address => "address",
            Limit::AddressHour => "address_hour",
            Limit::Hour => "hour",
        }
    }
}

struct Day {
    day: String,
    key: [u8; 32],
    counts: HashMap<Vec<u8>, u32>,
    /// An address's questions of the last hour.
    hours: HashMap<Vec<u8>, VecDeque<Instant>>,
}

pub struct Limits {
    day: Mutex<Day>,
    hour: Mutex<VecDeque<Instant>>,
}

/// Drop the times older than an hour before `now`.
fn prune(times: &mut VecDeque<Instant>, now: Instant) {
    while times
        .front()
        .is_some_and(|t| now.saturating_duration_since(*t) >= HOUR)
    {
        times.pop_front();
    }
}

impl Limits {
    pub fn new() -> Self {
        Self {
            day: Mutex::new(Day {
                day: String::new(),
                key: [0; 32],
                counts: HashMap::new(),
                hours: HashMap::new(),
            }),
            hour: Mutex::new(VecDeque::new()),
        }
    }

    /// Count a question from `address` with `user_agent` on `day` at `now`, or say which limit it
    /// ran into. A refused question takes no place.
    pub fn take(
        &self,
        day: &str,
        address: &str,
        user_agent: &str,
        now: Instant,
    ) -> Result<(), Limit> {
        let mut today = self.day.lock().expect("not poisoned");
        if today.day != day {
            let mut key = [0; 32];
            getrandom::fill(&mut key).expect("the operating system provides random bytes");
            *today = Day {
                day: day.to_string(),
                key,
                counts: HashMap::new(),
                hours: HashMap::new(),
            };
        }
        let network = network(address);
        let visitor = keyed(
            &today.key,
            &[b"visitor", network.as_bytes(), user_agent.as_bytes()],
        );
        let address = keyed(&today.key, &[b"address", network.as_bytes()]);
        if today.counts.get(&visitor).copied().unwrap_or(0) >= PER_VISITOR {
            return Err(Limit::Visitor);
        }
        if today.counts.get(&address).copied().unwrap_or(0) >= PER_ADDRESS {
            return Err(Limit::Address);
        }
        let recent = today.hours.entry(address.clone()).or_default();
        prune(recent, now);
        if recent.len() >= PER_ADDRESS_HOUR {
            return Err(Limit::AddressHour);
        }
        let mut hour = self.hour.lock().expect("not poisoned");
        prune(&mut hour, now);
        if hour.len() >= PER_HOUR {
            return Err(Limit::Hour);
        }
        hour.push_back(now);
        recent.push_back(now);
        *today.counts.entry(visitor).or_default() += 1;
        *today.counts.entry(address).or_default() += 1;
        Ok(())
    }
}

impl Default for Limits {
    fn default() -> Self {
        Self::new()
    }
}

/// The address limits count: an IPv4 address, or an IPv6 address's /64 network.
fn network(address: &str) -> String {
    match address.parse::<std::net::IpAddr>() {
        // an IPv4 client seen through an IPv6 socket
        Ok(std::net::IpAddr::V6(v6)) if v6.to_ipv4_mapped().is_some() => v6
            .to_ipv4_mapped()
            .map(|v4| v4.to_string())
            .unwrap_or_default(),
        Ok(std::net::IpAddr::V6(v6)) => {
            let s = v6.segments();
            format!("{:x}:{:x}:{:x}:{:x}::/64", s[0], s[1], s[2], s[3])
        }
        _ => address.to_string(),
    }
}

fn keyed(key: &[u8], parts: &[&[u8]]) -> Vec<u8> {
    let mut mac = <Hmac<Sha256> as KeyInit>::new_from_slice(key).expect("any key");
    for part in parts {
        mac.update(part);
        mac.update(b"\0");
    }
    mac.finalize().into_bytes().to_vec()
}

/// At most `per_minute` model calls in any minute, for a provider with a request limit.
pub struct CallRate {
    per_minute: Option<usize>,
    /// On tokio's clock, which tests can advance.
    calls: Mutex<VecDeque<tokio::time::Instant>>,
}

impl CallRate {
    pub fn new(per_minute: Option<u32>) -> Self {
        Self {
            per_minute: per_minute.map(|n| n.max(1) as usize),
            calls: Mutex::new(VecDeque::new()),
        }
    }

    /// Wait for a slot for one model call, at most `patience`; false when none came.
    pub async fn wait(&self, patience: Duration) -> bool {
        let Some(limit) = self.per_minute else {
            return true;
        };
        let started = tokio::time::Instant::now();
        loop {
            let wait = {
                let mut calls = self.calls.lock().expect("not poisoned");
                let now = tokio::time::Instant::now();
                while calls
                    .front()
                    .is_some_and(|t| now.duration_since(*t) >= Duration::from_secs(60))
                {
                    calls.pop_front();
                }
                if calls.len() < limit {
                    calls.push_back(now);
                    return true;
                }
                Duration::from_secs(60) - now.duration_since(calls[0])
            };
            if started.elapsed() + wait > patience {
                return false;
            }
            tokio::time::sleep(wait).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_visitor_an_address_and_the_hour_each_have_a_limit() {
        let limits = Limits::new();
        let start = Instant::now();
        // one question every 15 minutes stays within the address's hour
        let at = |n: u32| start + Duration::from_secs(u64::from(n) * 900);
        for n in 0..PER_VISITOR {
            assert_eq!(limits.take("2026-11-24", "a", "firefox", at(n)), Ok(()));
        }
        assert_eq!(
            limits.take("2026-11-24", "a", "firefox", at(PER_VISITOR)),
            Err(Limit::Visitor)
        );
        // another browser behind the same address, until the address is used up
        for n in PER_VISITOR..PER_ADDRESS {
            assert_eq!(limits.take("2026-11-24", "a", "chrome", at(n)), Ok(()));
        }
        assert_eq!(
            limits.take("2026-11-24", "a", "safari", at(PER_ADDRESS)),
            Err(Limit::Address)
        );
        // a new day starts afresh
        assert_eq!(limits.take("2026-11-25", "a", "firefox", at(100)), Ok(()));
        // an address has a share of the hour
        let limits = Limits::new();
        for n in 0..PER_ADDRESS_HOUR {
            assert_eq!(limits.take("d", "b", &n.to_string(), start), Ok(()));
        }
        assert_eq!(
            limits.take("d", "b", "other", start),
            Err(Limit::AddressHour)
        );
        // the hour counts every question of the site
        let limits = Limits::new();
        for n in 0..PER_HOUR {
            assert_eq!(limits.take("d", &n.to_string(), "x", start), Ok(()));
        }
        assert_eq!(limits.take("d", "new", "x", start), Err(Limit::Hour));
        assert_eq!(limits.take("d", "new", "x", start + HOUR), Ok(()));
    }

    #[test]
    fn an_ipv6_network_counts_as_one_address() {
        assert_eq!(
            network("2001:db8:1:2:aaaa::1"),
            network("2001:db8:1:2:bbbb::9")
        );
        assert_ne!(network("2001:db8:1:2::1"), network("2001:db8:1:3::1"));
        assert_eq!(network("192.0.2.1"), "192.0.2.1");
        assert_eq!(network("::ffff:192.0.2.1"), "192.0.2.1");
        assert_ne!(network("::ffff:192.0.2.1"), network("::ffff:192.0.2.2"));
        let limits = Limits::new();
        let now = Instant::now();
        for n in 0..PER_ADDRESS_HOUR {
            let address = format!("2001:db8:1:2::{n}");
            assert_eq!(limits.take("d", &address, "x", now), Ok(()));
        }
        assert_eq!(
            limits.take("d", "2001:db8:1:2::ff", "x", now),
            Err(Limit::AddressHour)
        );
    }

    #[tokio::test(start_paused = true)]
    async fn model_calls_wait_for_a_slot_or_give_up() {
        let rate = CallRate::new(Some(2));
        assert!(rate.wait(Duration::ZERO).await);
        assert!(rate.wait(Duration::ZERO).await);
        assert!(!rate.wait(Duration::from_secs(30)).await);
        assert!(rate.wait(Duration::from_secs(61)).await);
        assert!(CallRate::new(None).wait(Duration::ZERO).await);
    }
}
