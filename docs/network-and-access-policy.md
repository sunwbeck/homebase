# homebase Network and Access Policy

## Physical Network Layout

Current planned physical layout:

- router -> switch
- switch -> `workstation`
- switch -> `host`
- `control` -> Wi-Fi or Ethernet

## Network Intent

The network should support:

- stable local access between trusted nodes
- internal-only service exposure by default
- remote access through Tailscale rather than public exposure
- centralized operational control through `control`
- user-defined service exposure policy that can evolve without node-specific hardcoded rules

## Baseline Access Rules

- all administrative access should prefer flowing through `control`
- remote access from outside the home should go through Tailscale
- internal services should remain LAN-only unless there is a specific exception
- arbitrary direct exposure of services to the public internet is out of scope by default

These are baseline defaults, not a complete built-in taxonomy of every future exposure mode.

The system should not depend on a fixed list of policy names chosen in advance by the implementation.

## SSH Policy Intent

Target direction:

- `control` should become the main SSH management path
- predefined `homebase` CLI commands should be preferred for routine operations
- direct manual SSH access should be minimized where practical

Operational implication:

- the desired model is not just convenience but policy
- if this is to be enforced strongly, host-level firewall rules and SSH allow rules will need to be designed accordingly

## Known Access Exception

`workstation` intentionally exposes Sunshine for Moonlight-based streaming inside the LAN and for remote scenarios handled through the chosen secure access path.

This is an application-level exception and does not change the broader administrative access policy.

## Tailscale Policy

Remote access model:

- access the private environment through Tailscale
- avoid directly publishing administrative services to the internet
- keep the home network as the primary trust boundary

## User-Defined Exposure Groups

The preferred direction is to represent service reachability through user-defined exposure groups rather than through a small built-in set of policy names.

An exposure group is a named object defined by the operator.

It should express combinations such as:

- who can reach the service
- through which ingress node or path the service is exposed
- which network rules or filters are required
- whether the service remains internal, is shared more broadly, or is published through some approved edge path

Important design rules:

- the system must not assume names such as `public`, `tailnet`, or `shared` as the canonical model
- the operator should be free to define environment-specific groups that match actual usage and trust boundaries
- services should bind to those named groups instead of embedding firewall rules directly in service-specific logic

This keeps policy modular and lets the exposure model scale as new nodes, networks, and ingress requirements are introduced.

## Domain And Ingress Naming Direction

The preferred HTTP access pattern is subdomain-based rather than path-based.

Examples:

- `nextcloud.sunwoobeck.com`
- `yacreader.sunwoobeck.com`

Reasoning:

- many self-hosted applications behave more reliably on dedicated subdomains than on URL subpaths
- subdomains are easier to map cleanly through a reverse proxy
- subdomain routing makes it easier to change ingress realization later without rewriting application path assumptions

## Tailnet And `control` As One Ingress Path

Current likely direction:

- only `control` joins the tailnet for routine remote operations
- `host`, `host.app`, and other internal nodes remain LAN-only unless a separate reason justifies something else
- `control` can host a lightweight reverse proxy for services whose exposure group routes through `control`

Important limitation:

- this should be treated as one ingress implementation, not as a universal mandatory path for every service
- some services may later need a different exposure realization
- heavy or public traffic should not be forced through Raspberry Pi hardware if that becomes an operational bottleneck

Policy implications:

- Tailscale remains transport to `control`, not the universal policy language for the whole system
- Tailscale Funnel remains out of scope unless explicitly revisited later
- do not install Tailscale on every application VM merely to work around missing policy modeling

Backend exposure rules for services bound through `control`:

- services on `host.app` should remain reachable from `control` over the LAN
- where `control` is the selected ingress path, backend firewall rules should reflect that binding
- if an application trusts forwarded identity or auth headers, do not expose that application outside the intended proxy path

Expected normal access pattern:

- the client is on the same tailnet as `control`
- the service subdomain resolves to the ingress on `control`
- `control` reverse proxies the request to the intended backend on `host.app`

In this normal mode, service subdomains are not intended to be broadly reachable from the public internet.

## Temporary Public Exposure

Some real operator workflows require service access from a machine where Tailscale cannot reasonably be installed.

Examples include:

- using a shared or borrowed machine
- accessing a service from a printer-shop computer
- sharing one service briefly with another person

To support this without turning every service into a permanently public workload, the preferred direction is to support a temporary public exposure mode.

Expected behavior:

- a service is private by default
- the operator can intentionally switch one service into a public ingress mode through the `homebase` CLI
- the service can then be reached from the broader internet through its public ingress path
- the operator can later switch that same service back to private mode

Important policy rules:

- public exposure must be explicit, not the default
- public exposure should be scoped to one service at a time where possible
- public exposure should be easy to audit from grouped status output
- public exposure should be easy to revert
- a TTL-based auto-revert is preferred where practical
- additional access controls should be supported for sensitive services even during temporary public exposure

Recommended realization model:

- keep the domain names and ingress definitions ready in advance
- leave the public route inactive or blocked during normal operation
- activate the route only when the operator requests temporary exposure
- disable the route again once the task is complete or the TTL expires

## Status And Grouped Visibility

The operator should be able to inspect node exposure in a grouped way.

The intended status model is:

- group services by their assigned exposure group
- show which ports belong to which service
- show which node or ingress path currently exposes them
- show declared policy separately from realized state
- show whether a service is currently private-only or temporarily public
- show the active subdomain or ingress endpoint for the current mode

The main operational question should become:

- which services are reachable by whom right now

rather than only:

- which raw ports are listening

This grouped view is important for changing exposure safely and quickly.

It should support workflows such as:

- inspect all service exposure on `host.app`
- see that one service is currently reachable by a broader audience than intended
- rebind that service to a different user-defined exposure group
- reconcile the new declared policy into actual firewall and proxy state
- temporarily expose one service to the public internet
- confirm from status output that the service is now public
- revert that service to private-only mode after the task is complete

## Control Node Connectivity

Current decision state:

- `control` may use Wi-Fi or Ethernet
- the current control node hardware exposes both `eth0` and `wlan0`
- Wi-Fi is preferred for convenience if it does not impose meaningful technical or stability penalties
- Ethernet remains the safer choice if control-plane reliability becomes the priority

This remains an explicit open decision because `control` is intended to be the always-on management point.
