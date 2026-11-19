#include <arpa/inet.h>
#include <net/if.h>
#include <netinet/in.h>
#include <stdio.h>
#include <sys/socket.h>

#define OPT(fd, level, name, val) do { if (setsockopt(fd, level, name, &(val), sizeof(val))) perror(#name); else puts(#name " OK"); } while (0)

int main(void)
{
    int index = if_nametoindex("en8");
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    int yes = 1, hops = 255;
    unsigned char tiny_yes = 1, tiny_hops = 255;
    OPT(s, IPPROTO_IP, IP_BOUND_IF, index);
    OPT(s, IPPROTO_IP, IP_MULTICAST_TTL, tiny_hops);
    OPT(s, IPPROTO_IP, IP_TTL, hops);
    OPT(s, IPPROTO_IP, IP_MULTICAST_LOOP, tiny_yes);
    OPT(s, SOL_SOCKET, SO_REUSEADDR, yes);
    OPT(s, SOL_SOCKET, SO_REUSEPORT, yes);
    struct ip_mreqn group = { .imr_ifindex = index };
    group.imr_multiaddr.s_addr = inet_addr("224.0.0.251");
    OPT(s, IPPROTO_IP, IP_MULTICAST_IF, group);
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(5353) };
    if (bind(s, (struct sockaddr *)&a, sizeof(a))) perror("bind4"); else puts("bind4 OK");
    OPT(s, IPPROTO_IP, IP_ADD_MEMBERSHIP, group);
    struct ip_mreq group_addr = {0};
    group_addr.imr_multiaddr = group.imr_multiaddr;
    inet_pton(AF_INET,"192.168.1.110",&group_addr.imr_interface);
    OPT(s, IPPROTO_IP, IP_MULTICAST_IF, group_addr.imr_interface);
    OPT(s, IPPROTO_IP, IP_ADD_MEMBERSHIP, group_addr);
    int s6 = socket(AF_INET6, SOCK_DGRAM, 0);
    OPT(s6, IPPROTO_IPV6, IPV6_BOUND_IF, index);
    OPT(s6, IPPROTO_IPV6, IPV6_MULTICAST_HOPS, hops);
    OPT(s6, IPPROTO_IPV6, IPV6_UNICAST_HOPS, hops);
    OPT(s6, IPPROTO_IPV6, IPV6_V6ONLY, yes);
    OPT(s6, IPPROTO_IPV6, IPV6_MULTICAST_IF, index);
    OPT(s6, IPPROTO_IPV6, IPV6_MULTICAST_LOOP, yes);
    OPT(s6, SOL_SOCKET, SO_REUSEADDR, yes);
    OPT(s6, SOL_SOCKET, SO_REUSEPORT, yes);
    struct sockaddr_in6 a6 = { .sin6_family = AF_INET6, .sin6_port = htons(5353) };
    if (bind(s6, (struct sockaddr *)&a6, sizeof(a6))) perror("bind6"); else puts("bind6 OK");
    struct ipv6_mreq g6 = { .ipv6mr_interface = index };
    inet_pton(AF_INET6, "ff02::fb", &g6.ipv6mr_multiaddr);
    OPT(s6, IPPROTO_IPV6, IPV6_JOIN_GROUP, g6);
    return 0;
}
